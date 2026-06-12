FROM edth-base:latest

WORKDIR /app

# Install contracts (shared dep) then sim
COPY contracts/ ./contracts/
COPY sim/       ./sim/
COPY config/    ./config/

RUN source /opt/ros/jazzy/setup.bash \
    && uv pip install --system -e ./contracts -e ./sim

CMD ["python3", "-m", "sim.world"]
