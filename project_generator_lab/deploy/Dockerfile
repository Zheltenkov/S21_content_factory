# Use the verified production image's installed Python libraries; no main-app rebuild.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
WORKDIR /app
COPY project_generator_lab /app/project_generator_lab
RUN mkdir -p /data/project-lab/runs && chown -R 10001:10001 /data/project-lab
USER 10001:10001
CMD ["python", "-m", "uvicorn", "project_generator_lab.production:app", "--host", "0.0.0.0", "--port", "8011", "--proxy-headers", "--forwarded-allow-ips", "*", "--root-path", "/app/project-preparation"]
