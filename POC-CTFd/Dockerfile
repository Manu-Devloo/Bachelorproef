FROM ctfd/ctfd:3.8.1

USER root

RUN pip install --no-cache-dir docker==7.1.0

COPY plugins/ctfd_container_challenges /opt/CTFd/CTFd/plugins/ctfd_container_challenges

WORKDIR /opt/CTFd
