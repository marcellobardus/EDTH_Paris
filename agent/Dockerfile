FROM edth-base:latest

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY contracts/ ./contracts/
COPY agent/     ./agent/
COPY config/    ./config/

RUN source /opt/ros/jazzy/setup.bash \
    && uv pip install --system -e ./contracts -e ./agent

# INTERCEPTOR_ID must be set at runtime: docker run -e INTERCEPTOR_ID=i1 ...
ENV INTERCEPTOR_ID=i1
CMD ["python3", "-m", "agent.interceptor_agent"]
