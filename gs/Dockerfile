FROM edth-base:latest

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY contracts/ ./contracts/
COPY gs/        ./gs/
COPY config/    ./config/

RUN source /opt/ros/jazzy/setup.bash \
    && uv pip install --system -e ./contracts -e ./gs

CMD ["python3", "-m", "gs.track_fusion"]
