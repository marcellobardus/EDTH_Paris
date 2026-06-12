FROM ros:jazzy

# Gazebo Harmonic + ROS-GZ bridge
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gz-harmonic \
    ros-jazzy-ros-gz-bridge \
    ros-jazzy-ros-gz-sim \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# uv for fast Python installs inside containers
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Source ROS2 on every bash session
RUN echo "source /opt/ros/jazzy/setup.bash" >> /root/.bashrc

SHELL ["/bin/bash", "-c"]
ENTRYPOINT ["/ros_entrypoint.sh"]
