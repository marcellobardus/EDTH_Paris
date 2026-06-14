"""
Canonical topic/channel names. Import from here — never hardcode strings.
For topics with {id}, call Topics.interceptor_state("i1") etc.
"""


class Topics:
    # Simulation → Ground Station
    RADAR_DETECTIONS = "/radar/detections"  # RadarDetection[]

    # Ground Station internal
    GS_TRACKS = "/gs/tracks"  # Track[]
    GS_THREATS = "/gs/threats"  # ThreatAssessment[]

    # Ground Station → Interceptors (at launch)
    GS_ASSIGNMENTS = "/gs/assignments"  # Assignment[]

    # Simulation → Visualization
    GROUND_TRUTH = "/simulation/ground_truth"  # GroundTruth
    ENGAGEMENT = "/simulation/engagement"  # EngagementEvent

    # Visualization → Simulation (control)
    RESET = "/simulation/reset"  # std_msgs/String (empty) — respawn the scenario

    # Interceptor peer-to-peer (parametric — use helpers below)
    @staticmethod
    def interceptor_state(interceptor_id: str) -> str:
        return f"/interceptors/{interceptor_id}/state"  # InterceptorState

    @staticmethod
    def interceptor_claim(interceptor_id: str) -> str:
        return f"/interceptors/{interceptor_id}/claim"  # Claim

    @staticmethod
    def interceptor_commit(interceptor_id: str) -> str:
        return f"/interceptors/{interceptor_id}/commit"  # Commit

    # Interceptor → Simulation
    @staticmethod
    def waypoint_command(interceptor_id: str) -> str:
        return f"/interceptors/{interceptor_id}/waypoint"  # WaypointCommand
