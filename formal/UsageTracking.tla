---------------------------- MODULE UsageTracking ----------------------------
EXTENDS Naturals

CONSTANT Tokens
VARIABLES phase, enabled, stored, host, protected, keys
vars == <<phase, enabled, stored, host, protected, keys>>

Init == /\ phase = "prepared"
        /\ enabled = FALSE
        /\ stored = 0
        /\ host = "unconfigured"
        /\ protected = FALSE
        /\ keys = "existing"

Enable == /\ phase = "prepared"
          /\ phase' = "enabled"
          /\ enabled' = TRUE
          /\ host' = "127.0.0.1"
          /\ protected' = TRUE
          /\ UNCHANGED <<stored, keys>>

Commit == /\ phase = "enabled"
          /\ enabled
          /\ phase' = "committed"
          /\ stored' = Tokens
          /\ UNCHANGED <<enabled, host, protected, keys>>

Restart == /\ phase = "committed"
           /\ phase' = "verified"
           /\ stored' = stored
           /\ UNCHANGED <<enabled, host, protected, keys>>

Finish == /\ phase = "verified"
          /\ UNCHANGED vars

Next == Enable \/ Commit \/ Restart \/ Finish
Spec == Init /\ [][Next]_vars
EnabledBeforeCommit == stored > 0 => enabled
DurableTotals == phase = "verified" => stored = Tokens
PrivateAccess == phase # "prepared" => host = "127.0.0.1" /\ protected /\ keys = "existing"
=============================================================================
