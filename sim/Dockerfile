FROM edth-base:latest

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY contracts/ ./contracts/
COPY sim/       ./sim/
COPY config/    ./config/

RUN source /opt/ros/jazzy/setup.bash \
    && uv pip install --system -e ./contracts -e ./sim

EXPOSE 9002

CMD ["python3", "-m", "sim.world"]
