FROM python:3.12-slim-bookworm

ARG SANDBOX_UID=65532
ARG SANDBOX_GID=65532

RUN groupadd --gid "${SANDBOX_GID}" sandbox \
    && useradd --uid "${SANDBOX_UID}" --gid "${SANDBOX_GID}" \
       --no-create-home --shell /usr/sbin/nologin sandbox

ENV LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace
USER 65532:65532

CMD ["python", "--version"]
