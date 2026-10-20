# The viewer and the Python tools, CPU only. External detectors (fpocket,
# lacuna, NEAT-POCKET) are not in the image; the viewer reports them as
# unavailable, as it does on any machine without them.
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY proteinviewer proteinviewer
COPY pocketbench pocketbench
COPY pocketgen pocketgen
COPY quickprep quickprep
COPY serve.py ./
RUN pip install --no-cache-dir .

# Inside a container the server must listen on all interfaces; publish the
# port on loopback only (-p 127.0.0.1:8000:8000) unless you mean to share it.
ENV PV_HOST=0.0.0.0 \
    PV_PROJECT_DIR=/data/projects
VOLUME /data
EXPOSE 8000
RUN useradd --create-home viewer && mkdir -p /data && chown viewer /data
USER viewer
ENTRYPOINT ["python3", "serve.py", "--no-open"]
