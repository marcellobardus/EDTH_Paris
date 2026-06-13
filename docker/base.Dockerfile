FROM ros:jazzy

# Add OSRF apt repo (required for gz-harmonic)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    gnupg \
    lsb-release \
    && curl -sSL https://packages.osrfoundation.org/gazebo.gpg \
       -o /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] \
       http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
       > /etc/apt/sources.list.d/gazebo-stable.list \
    && rm -rf /var/lib/apt/lists/*

# Gazebo Harmonic + ROS-GZ bridge
RUN apt-get update && apt-get install -y --no-install-recommends \
    gz-harmonic \
    ros-jazzy-ros-gz-bridge \
    ros-jazzy-ros-gz-sim \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# PX4-gazebo-models: provides quadtailsitter, standard_vtol, etc. for Gazebo Harmonic SITL
RUN git clone --depth 1 https://github.com/PX4/PX4-gazebo-models.git /opt/px4-gazebo-models
ENV GZ_SIM_RESOURCE_PATH=/opt/px4-gazebo-models/models

# uv for fast Python installs inside containers
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
# Allow uv to install into Ubuntu 24.04's externally-managed system Python
ENV UV_BREAK_SYSTEM_PACKAGES=1

# Source ROS2 on every bash session
RUN echo "source /opt/ros/jazzy/setup.bash" >> /root/.bashrc

SHELL ["/bin/bash", "-c"]
ENTRYPOINT ["/ros_entrypoint.sh"]
