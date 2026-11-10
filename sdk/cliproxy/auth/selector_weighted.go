package auth

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"math"
	"sort"
	"time"

	cliproxyexecutor "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/executor"
)

type weightedAccountingPoolKey struct{}

type weightedTransportProviderKey struct{}

type weightedReservation struct {
	selector   *WeightedRoundRobinSelector
	state      *weightedPoolState
	generation uint64
	member     int
	total      int64
	bound      bool
	finished   bool
}

func (s *SessionAffinitySelector) pickWithAffinityReservation(ctx context.Context, provider, model string, opts cliproxyexecutor.Options, auths []*Auth, bound *Auth, reservation **weightedReservation) (*Auth, error) {
	weighted, ok := s.fallback.(*WeightedRoundRobinSelector)
	if !ok {
		if bound != nil {
			return bound, nil
		}
		return s.fallback.Pick(ctx, provider, model, opts, auths)
	}
	picked, pending, errPick := weighted.reserveForRequest(ctx, provider, model, opts, auths, bound)
	*reservation = pending
	return picked, errPick
}

func (s *WeightedRoundRobinSelector) reserveForRequest(ctx context.Context, provider, model string, opts cliproxyexecutor.Options, auths []*Auth, bound *Auth) (*Auth, *weightedReservation, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	if errContext := ctx.Err(); errContext != nil {
		return nil, nil, errContext
	}
	if accountingAuths, ok := ctx.Value(weightedAccountingPoolKey{}).([]*Auth); ok {
		pinnedID := pinnedAuthIDFromMetadata(opts.Metadata)
		if bound == nil {
			for _, auth := range auths {
				if auth != nil && auth.ID == pinnedID {
					bound = auth
					break
				}
			}
		}
		if bound == nil || bound.ID != pinnedID || authWeight(bound) <= 0 {
			return nil, nil, &Error{Code: "auth_unavailable", Message: "pinned auth is not available for weighted accounting"}
		}
		auths = positiveWeightAuths(accountingAuths)
	}
	transportProvider := provider
	if singleProvider, ok := ctx.Value(weightedTransportProviderKey{}).(string); ok {
		transportProvider = singleProvider
	}
	if providerPrefersWebsocketTransport(transportProvider) {
		transportProvider = "codex"
	}
	var available []*Auth
	authID := ""
	if bound == nil {
		var errAvailable error
		available, errAvailable = getSelectorAvailableAuths(ctx, positiveWeightAuths(auths), provider, model, time.Now())
		if errAvailable != nil {
			return nil, nil, errAvailable
		}
		available = preferCodexWebsocketAuths(ctx, transportProvider, available)
	} else {
		authID = bound.ID
		priority := authPriority(bound)
		for _, auth := range auths {
			if authPriority(auth) == priority {
				available = append(available, auth)
			}
		}
		preferred := preferCodexWebsocketAuths(ctx, transportProvider, available)
		if len(preferred) < len(available) && !authWebsocketsEnabled(bound) {
			nonWebsocket := available[:0]
			for _, auth := range available {
				if !authWebsocketsEnabled(auth) {
					nonWebsocket = append(nonWebsocket, auth)
				}
			}
			available = nonWebsocket
		} else {
			available = preferred
		}
	}
	key := provider + ":" + canonicalModelLookupKey(weightedSelectorStateModel(ctx, model))
	picked, pending := s.reserve(key, available, authID)
	if picked == nil {
		return nil, nil, &Error{Code: "auth_unavailable", Message: "no auth available with positive weight"}
	}
	return picked, pending, nil
}

type weightedPoolMember struct {
	id      string
	weight  int64
	current int64
	pending int64
}

type weightedPoolState struct {
	route      string
	members    []weightedPoolMember
	inFlight   uint64
	generation uint64
	next       *weightedPoolState
}

func sortedWeightedIDs(weights map[string]int64) []string {
	ids := make([]string, 0, len(weights))
	for id := range weights {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}

func weightedPoolStateKey(route string, weights map[string]int64) [32]byte {
	return weightedPoolDigest(route, sortedWeightedIDs(weights))
}

func weightedPoolDigest(route string, ids []string) [32]byte {
	size := 8 + len(route)
	for _, id := range ids {
		size += 8 + len(id)
	}
	identity := make([]byte, 0, size)
	identity = binary.LittleEndian.AppendUint64(identity, uint64(len(route)))
	identity = append(identity, route...)
	for _, id := range ids {
		identity = binary.LittleEndian.AppendUint64(identity, uint64(len(id)))
		identity = append(identity, id...)
	}
	return sha256.Sum256(identity)
}

func (s *weightedPoolState) matches(route string, ids []string) bool {
	if s.route != route || len(s.members) != len(ids) {
		return false
	}
	for i, id := range ids {
		if s.members[i].id != id {
			return false
		}
	}
	return true
}

func (s *weightedPoolState) prepare(weights []int64) {
	for i, member := range s.members {
		if member.weight != weights[i] {
			s.generation++
			s.inFlight = 0
			for i := range s.members {
				s.members[i].weight = weights[i]
				s.members[i].current = 0
				s.members[i].pending = 0
			}
			return
		}
	}
}

func (s *WeightedRoundRobinSelector) reserve(route string, available []*Auth, authID string) (*Auth, *weightedReservation) {
	weightVector := authWeightVector(available)
	ids := sortedWeightedIDs(weightVector)
	weights := make([]int64, len(ids))
	for i, id := range ids {
		weights[i] = weightVector[id]
	}
	return s.reservePool(route, available, weights, ids, authID)
}

func (s *WeightedRoundRobinSelector) reservePool(route string, available []*Auth, weights []int64, ids []string, authID string) (*Auth, *weightedReservation) {
	key := weightedPoolDigest(route, ids)
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.states == nil {
		s.states = make(map[[32]byte]*weightedPoolState)
	}
	state := s.states[key]
	for state != nil && !state.matches(route, ids) {
		state = state.next
	}
	if state == nil {
		limit := s.maxKeys
		if limit <= 0 {
			limit = 4096
		}
		if s.stateCount >= limit {
			s.states = make(map[[32]byte]*weightedPoolState)
			s.stateCount = 0
		}
		state = &weightedPoolState{route: route, members: make([]weightedPoolMember, len(ids)), next: s.states[key]}
		for i, id := range ids {
			state.members[i] = weightedPoolMember{id: id, weight: weights[i]}
		}
		s.states[key] = state
		s.stateCount++
	} else {
		state.prepare(weights)
	}
	var picked *Auth
	var pickedCurrent, total int64
	pickedMember := -1
	for _, auth := range available {
		if auth == nil {
			continue
		}
		index := sort.SearchStrings(ids, auth.ID)
		if index == len(ids) || ids[index] != auth.ID || weights[index] <= 0 {
			continue
		}
		total = saturatingAddInt64(total, weights[index])
		if authID != "" && auth.ID != authID {
			continue
		}
		member := &state.members[index]
		current := saturatingAddInt64(member.current, member.pending)
		current = saturatingAddInt64(current, member.weight)
		if picked == nil || current > pickedCurrent {
			picked, pickedCurrent, pickedMember = auth, current, index
		}
	}
	if picked == nil {
		return nil, nil
	}
	if authID != "" && !state.boundChargeFits(pickedMember, total) {
		return picked, nil
	}
	state.adjustPending(pickedMember, total, false)
	state.inFlight++
	return picked, &weightedReservation{
		selector: s, state: state, generation: state.generation,
		member: pickedMember, total: total, bound: authID != "",
	}
}

func (r *weightedReservation) finish(committed bool) {
	if r == nil {
		return
	}
	r.selector.mu.Lock()
	defer r.selector.mu.Unlock()
	if r.finished {
		return
	}
	r.finished = true
	if r.state.generation != r.generation {
		return
	}
	r.state.adjustPending(r.member, r.total, true)
	r.state.inFlight--
	if committed {
		r.state.charge(r.member, r.bound)
	} else if r.state.inFlight == 0 {
		r.state.boundCredits(r.total)
	}
}

func (s *weightedPoolState) adjustPending(selected int, total int64, release bool) {
	for i := range s.members {
		member := &s.members[i]
		delta := member.weight
		if i == selected {
			delta -= total
		}
		if release {
			delta = -delta
		}
		member.pending = saturatingAddInt64(member.pending, delta)
	}
}

const maxSmoothWeightedCreditRounds int64 = 32

func (s *weightedPoolState) boundChargeFits(selected int, totalWeight int64) bool {
	limit := int64(math.MaxInt64)
	if totalWeight <= math.MaxInt64/maxSmoothWeightedCreditRounds {
		limit = totalWeight * maxSmoothWeightedCreditRounds
	}
	for i, member := range s.members {
		delta := member.weight
		if i == selected {
			delta -= totalWeight
		}
		current := saturatingAddInt64(member.current, member.pending)
		next := saturatingAddInt64(current, delta)
		if next < -limit || next > limit {
			return false
		}
	}
	return true
}

func (s *weightedPoolState) charge(selected int, bound bool) {
	var totalWeight int64
	for _, member := range s.members {
		totalWeight = saturatingAddInt64(totalWeight, member.weight)
	}
	if !bound || s.boundChargeFits(selected, totalWeight) {
		for i := range s.members {
			member := &s.members[i]
			member.current = saturatingAddInt64(member.current, member.weight)
		}
		member := &s.members[selected]
		member.current = saturatingAddInt64(member.current, -totalWeight)
	}
	if s.inFlight == 0 {
		s.boundCredits(totalWeight)
	}
}

func (s *weightedPoolState) boundCredits(totalWeight int64) {
	limit := int64(math.MaxInt64)
	if totalWeight <= math.MaxInt64/maxSmoothWeightedCreditRounds {
		limit = totalWeight * maxSmoothWeightedCreditRounds
	}
	var minCredit, maxCredit int64 = math.MaxInt64, math.MinInt64
	for _, member := range s.members {
		minCredit = min(minCredit, member.current)
		maxCredit = max(maxCredit, member.current)
	}
	minOffset := saturatingAddInt64(maxCredit, -limit)
	maxOffset := saturatingAddInt64(minCredit, limit)
	var offset int64
	if minOffset > 0 || minOffset > maxOffset {
		offset = minOffset
	} else if maxOffset < 0 {
		offset = maxOffset
	}
	for i := range s.members {
		member := &s.members[i]
		member.current = max(-limit, saturatingAddInt64(member.current, -offset))
	}
}
