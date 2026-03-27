"""Container backends for the CTFd plugin PoC."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import random
from typing import Iterable
from typing import Protocol
from uuid import uuid4

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from docker.tls import TLSConfig

LOGGER = logging.getLogger(__name__)


class BackendError(RuntimeError):
    pass


@dataclass(frozen=True)
class StartedContainer:
    container_id: str
    host_port: int


@dataclass(frozen=True)
class PublishedPortRange:
    start: int
    end: int

    def ordered_candidates(self) -> Iterable[int]:
        size = (self.end - self.start) + 1
        offset = random.randint(0, size - 1)
        for index in range(size):
            yield self.start + ((offset + index) % size)


class ContainerBackend(Protocol):
    def import_image_archive(self, *, archive_path: str, image_tag: str) -> str:
        ...

    def start(
        self,
        *,
        instance_name: str,
        image: str,
        network_name: str,
        container_port: int,
        cpu_limit: float,
        memory_limit_mb: int,
        archive_path: str | None,
        labels: dict[str, str],
        published_port_range: PublishedPortRange | None,
    ) -> StartedContainer:
        ...

    def stop(self, *, container_id: str, network_name: str) -> None:
        ...

    def health(self) -> dict[str, str]:
        ...


class DockerBackend:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        tls_config: TLSConfig | bool | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        client_kwargs = {"timeout": max(int(timeout_seconds), 5)}
        if base_url:
            client_kwargs["base_url"] = base_url
            if tls_config is not None:
                client_kwargs["tls"] = tls_config
            self.client = docker.DockerClient(**client_kwargs)
            self._target = base_url
        else:
            self.client = docker.from_env(**client_kwargs)
            self._target = "environment"

    def import_image_archive(self, *, archive_path: str, image_tag: str) -> str:
        try:
            with open(archive_path, "rb") as handle:
                loaded = self.client.images.load(handle.read())
        except (OSError, APIError, DockerException) as exc:
            raise BackendError(str(exc)) from exc

        if not loaded:
            raise BackendError(f"No image found in archive '{archive_path}'")

        image = loaded[0]
        try:
            if ":" in image_tag:
                repository, tag = image_tag.rsplit(":", 1)
                image.tag(repository, tag=tag)
            else:
                image.tag(image_tag)
        except DockerException as exc:
            raise BackendError(str(exc)) from exc
        return image_tag

    def start(
        self,
        *,
        instance_name: str,
        image: str,
        network_name: str,
        container_port: int,
        cpu_limit: float,
        memory_limit_mb: int,
        archive_path: str | None,
        labels: dict[str, str],
        published_port_range: PublishedPortRange | None,
    ) -> StartedContainer:
        network = None
        container = None
        try:
            try:
                self.client.images.get(image)
            except ImageNotFound:
                if archive_path:
                    self.import_image_archive(archive_path=archive_path, image_tag=image)
                else:
                    self.client.images.pull(image)

            network = self.client.networks.create(
                name=network_name,
                driver="bridge",
                check_duplicate=True,
                labels=labels,
            )
            for requested_port in self._iter_host_ports(published_port_range):
                try:
                    container = self.client.containers.run(
                        image=image,
                        name=instance_name,
                        detach=True,
                        network=network_name,
                        ports={
                            f"{container_port}/tcp": (
                                "0.0.0.0",
                                requested_port,
                            )
                            if requested_port is not None
                            else None
                        },
                        mem_limit=f"{memory_limit_mb}m",
                        nano_cpus=max(int(cpu_limit * 1_000_000_000), 100_000_000),
                        read_only=True,
                        pids_limit=128,
                        cap_drop=["ALL"],
                        security_opt=["no-new-privileges:true"],
                        tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
                        labels=labels,
                    )
                except APIError as exc:
                    if requested_port is not None and self._is_port_conflict(exc):
                        continue
                    raise

                container.reload()
                port_binding = container.attrs["NetworkSettings"]["Ports"].get(
                    f"{container_port}/tcp"
                )
                if not port_binding:
                    raise BackendError("Container started without a published host port")

                host_port = int(port_binding[0]["HostPort"])
                return StartedContainer(container_id=container.id, host_port=host_port)

            raise BackendError(
                "No free host port available in the configured published port range"
            )
        except (APIError, DockerException, KeyError, ValueError) as exc:
            if container is not None:
                try:
                    container.remove(force=True)
                except DockerException:
                    LOGGER.exception("failed to cleanup container after startup error")
            if network is not None:
                try:
                    network.remove()
                except DockerException:
                    LOGGER.exception("failed to cleanup network after startup error")
            raise BackendError(str(exc)) from exc

    def stop(self, *, container_id: str, network_name: str) -> None:
        try:
            container = self.client.containers.get(container_id)
            container.remove(force=True)
        except NotFound:
            LOGGER.info("container %s already gone", container_id)
        except DockerException as exc:
            raise BackendError(str(exc)) from exc

        try:
            network = self.client.networks.get(network_name)
            network.remove()
        except NotFound:
            LOGGER.info("network %s already gone", network_name)
        except DockerException as exc:
            raise BackendError(str(exc)) from exc

    def health(self) -> dict[str, str]:
        try:
            self.client.ping()
            return {"backend": "docker", "status": "ok", "target": self._target}
        except DockerException as exc:
            return {
                "backend": "docker",
                "status": f"error: {exc}",
                "target": self._target,
            }

    @staticmethod
    def _iter_host_ports(
        published_port_range: PublishedPortRange | None,
    ) -> Iterable[int | None]:
        if published_port_range is None:
            yield None
            return
        yield from published_port_range.ordered_candidates()

    @staticmethod
    def _is_port_conflict(exc: APIError) -> bool:
        message = str(exc).lower()
        conflict_markers = (
            "port is already allocated",
            "address already in use",
            "bind for 0.0.0.0:",
            "failed programming external connectivity",
        )
        return any(marker in message for marker in conflict_markers)


class InMemoryBackend:
    """Small mock backend for dry-runs without Docker."""

    def __init__(self) -> None:
        self._containers: dict[str, dict[str, str | int]] = {}

    def start(
        self,
        *,
        instance_name: str,
        image: str,
        network_name: str,
        container_port: int,
        cpu_limit: float,
        memory_limit_mb: int,
        archive_path: str | None,
        labels: dict[str, str],
        published_port_range: PublishedPortRange | None,
    ) -> StartedContainer:
        container_id = f"mock-{uuid4().hex[:12]}"
        if published_port_range is not None:
            host_port = next(published_port_range.ordered_candidates())
        else:
            host_port = random.randint(30000, 45000)
        self._containers[container_id] = {
            "instance_name": instance_name,
            "image": image,
            "network_name": network_name,
            "container_port": container_port,
            "cpu_limit": cpu_limit,
            "memory_limit_mb": memory_limit_mb,
            "host_port": host_port,
            "labels": str(labels),
        }
        return StartedContainer(container_id=container_id, host_port=host_port)

    def import_image_archive(self, *, archive_path: str, image_tag: str) -> str:
        return image_tag

    def stop(self, *, container_id: str, network_name: str) -> None:
        self._containers.pop(container_id, None)

    def health(self) -> dict[str, str]:
        return {"backend": "mock", "status": "ok"}
