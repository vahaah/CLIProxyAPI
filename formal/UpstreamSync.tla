----------------------------- MODULE UpstreamSync -----------------------------
VARIABLES phase, binary, previous, configuration, credentials

vars == <<phase, binary, previous, configuration, credentials>>

Init == /\ phase = "ready"
        /\ binary = "old"
        /\ previous = "none"
        /\ configuration = "existing"
        /\ credentials = "existing"

Build == /\ phase = "ready"
         /\ phase' = "built"
         /\ UNCHANGED <<binary, previous, configuration, credentials>>

Reject == /\ phase = "ready"
          /\ phase' = "rejected"
          /\ UNCHANGED <<binary, previous, configuration, credentials>>

Install == /\ phase = "built"
           /\ phase' = "installed"
           /\ binary' = "new"
           /\ previous' = binary
           /\ configuration' = configuration
           /\ UNCHANGED credentials

Restart == /\ phase = "installed"
           /\ phase' = "running"
           /\ UNCHANGED <<binary, previous, configuration, credentials>>

Finish == /\ phase \in {"running", "rejected"}
          /\ UNCHANGED vars

Next == Build \/ Reject \/ Install \/ Restart \/ Finish
Spec == Init /\ [][Next]_vars

PrivateStatePreserved == configuration = "existing" /\ credentials = "existing"
RollbackAvailable == binary = "new" => previous = "old"
RejectedBuildPreserved == phase = "rejected" => binary = "old"
ActivationOrdered == phase = "running" => binary = "new"
=============================================================================
