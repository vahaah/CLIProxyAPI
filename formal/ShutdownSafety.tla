---------------------------- MODULE ShutdownSafety ----------------------------
VARIABLES phase, mode, httpOpen, resourcesOpen, completed
vars == <<phase, mode, httpOpen, resourcesOpen, completed>>

Graceful == "graceful"
Immediate == "immediate"
Exhausted == "exhausted"

Init == /\ phase = "active"
        /\ mode \in {Graceful, Immediate, Exhausted}
        /\ httpOpen = TRUE
        /\ resourcesOpen = TRUE
        /\ completed = FALSE

RequestShutdown == /\ phase = "active"
                   /\ phase' = "requested"
                   /\ resourcesOpen' = resourcesOpen
                   /\ UNCHANGED <<mode, httpOpen, completed>>

BeginDrain == /\ phase = "requested"
              /\ mode # Immediate
              /\ phase' = "draining"
              /\ UNCHANGED <<mode, httpOpen, resourcesOpen, completed>>

Complete == /\ phase = "draining"
            /\ mode = Graceful
            /\ phase' = "http-closed"
            /\ httpOpen' = FALSE
            /\ completed' = TRUE
            /\ UNCHANGED <<mode, resourcesOpen>>

ForceClose == /\ (phase = "requested" /\ mode = Immediate)
                  \/ (phase = "draining" /\ mode = Exhausted)
              /\ phase' = "http-closed"
              /\ httpOpen' = FALSE
              /\ UNCHANGED <<mode, resourcesOpen, completed>>

Teardown == /\ phase = "http-closed"
            /\ phase' = "stopped"
            /\ resourcesOpen' = FALSE
            /\ UNCHANGED <<mode, httpOpen, completed>>

Finish == /\ phase = "stopped"
          /\ UNCHANGED vars

Next == RequestShutdown \/ BeginDrain \/ Complete \/ ForceClose \/ Teardown \/ Finish
Spec == Init /\ [][Next]_vars
ResourcesOutliveHTTP == httpOpen => resourcesOpen
SuccessfulDrain == (phase = "stopped" /\ mode = Graceful) => completed
ImmediateSkipsDrain == mode = Immediate => phase # "draining"
=============================================================================
