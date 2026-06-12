FROM edth-base:latest

WORKDIR /app

COPY contracts/ ./contracts/
COPY viz/       ./viz/
COPY config/    ./config/

RUN source /opt/ros/jazzy/setup.bash \
    && uv pip install --system -e ./contracts -e ./viz

CMD ["python3", "-m", "viz.dashboard"]
