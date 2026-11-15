---------------------------- MODULE UsageTracking ----------------------------
EXTENDS Naturals

VARIABLES phase, enabled, stored, host, protected, keys
vars == <<phase, enabled, stored, host, protected, keys>>

Init == /\ phase = "prepared"
        /\ enabled = FALSE
        /\ stored = 0
        /\ host = "127.0.0.1"
        /\ protected = TRUE
        /\ keys = "existing"

Enable == /\ phase = "prepared"
          /\ phase' = "enabled"
          /\ enabled' = TRUE
          /\ UNCHANGED <<stored, host, protected, keys>>

Commit == /\ phase = "enabled"
          /\ enabled
          /\ phase' = "committed"
          /\ stored' = 143
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
DurableTotals == phase = "verified" => stored = 143
PrivateAccess == host = "127.0.0.1" /\ protected /\ keys = "existing"
=============================================================================
