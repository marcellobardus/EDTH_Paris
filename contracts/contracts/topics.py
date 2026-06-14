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

    # Interceptor peer-to-peer (parametric — use helpers below).
    # CBAA folds everything into one channel: there is no claim/commit topic,
    # the InterceptorState broadcast carries ownership + priority + lock.
    @staticmethod
    def interceptor_state(interceptor_id: str) -> str:
        return f"/interceptors/{interceptor_id}/state"  # InterceptorState

    # Interceptor → Simulation
    @staticmethod
    def waypoint_command(interceptor_id: str) -> str:
        return f"/interceptors/{interceptor_id}/waypoint"  # WaypointCommand
