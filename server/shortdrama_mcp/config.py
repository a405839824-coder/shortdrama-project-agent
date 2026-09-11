from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def _admin_subjects_from_env() -> tuple[str, ...]:
    subjects = json.loads(os.getenv("SHORTDRAMA_ADMIN_SUBJECTS", "[]"))
    if not isinstance(subjects, list):
        raise ValueError("SHORTDRAMA_ADMIN_SUBJECTS must be a JSON array of OAuth subject strings")
    return tuple(subjects)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    public_base_url: str
    host: str
    port: int
    deployment_mode: str
    auth_mode: str
    oauth_issuer: str | None
    oauth_audience: str | None
    oauth_jwks_url: str | None
    oauth_scopes: tuple[str, ...]
    allow_local_file_urls: bool
    max_file_bytes: int
    admin_subjects: tuple[str, ...] = ()
    acceptance_control_enabled: bool = False
    acceptance_registry_dir: Path | None = None
    acceptance_lease_id: str | None = None
    member_connection_access: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        plugin_root = Path(__file__).resolve().parents[2]
        default_data_dir = os.getenv("PLUGIN_DATA") or str(plugin_root / "data")
        data_dir = Path(os.getenv("SHORTDRAMA_DATA_DIR", default_data_dir)).expanduser().resolve()
        host = os.getenv("HOST", "127.0.0.1")
        port = int(os.getenv("PORT", "8000"))
        public_base_url = os.getenv("PUBLIC_BASE_URL", f"http://127.0.0.1:{port}").rstrip("/")
        deployment_mode = os.getenv("SHORTDRAMA_DEPLOYMENT_MODE", "local").strip().lower()
        auth_mode = os.getenv("SHORTDRAMA_AUTH_MODE", "none").strip().lower()
        acceptance_flag = os.getenv("SHORTDRAMA_ACCEPTANCE_CONTROL", "0").strip().lower()
        member_flag = os.getenv("SHORTDRAMA_MEMBER_CONNECTION_ACCESS", "0").strip().lower()
        if member_flag not in {"0", "1", "false", "true"}:
            raise ValueError("SHORTDRAMA_MEMBER_CONNECTION_ACCESS must be 0/1 or false/true")
        if acceptance_flag not in {"0", "1", "false", "true"}:
            raise ValueError("SHORTDRAMA_ACCEPTANCE_CONTROL must be 0/1 or false/true")
        oauth_scopes = tuple(
            item.strip()
            for item in os.getenv("OAUTH_SCOPES", "storyboard:read,storyboard:write").split(",")
            if item.strip()
        )
        settings = cls(
            data_dir=data_dir,
            public_base_url=public_base_url,
            host=host,
            port=port,
            deployment_mode=deployment_mode,
            auth_mode=auth_mode,
            oauth_issuer=os.getenv("OAUTH_ISSUER"),
            oauth_audience=os.getenv("OAUTH_AUDIENCE"),
            oauth_jwks_url=os.getenv("OAUTH_JWKS_URL"),
            oauth_scopes=oauth_scopes,
            allow_local_file_urls=os.getenv("ALLOW_LOCAL_FILE_URLS", "0") == "1",
            max_file_bytes=int(os.getenv("MAX_FILE_BYTES", str(50 * 1024 * 1024))),
            admin_subjects=_admin_subjects_from_env(),
            acceptance_control_enabled=acceptance_flag in {"1", "true"},
            acceptance_registry_dir=(Path(os.environ["SHORTDRAMA_ACCEPTANCE_REGISTRY"]).expanduser().resolve()
                                     if os.getenv("SHORTDRAMA_ACCEPTANCE_REGISTRY") else None),
            acceptance_lease_id=os.getenv("SHORTDRAMA_ACCEPTANCE_LEASE") or None,
            member_connection_access=member_flag in {"1", "true"},
        )
        settings.validate()
        return settings

    def validate_acceptance_control(self) -> None:
        if not isinstance(self.acceptance_control_enabled, bool):
            raise ValueError("acceptance_control_enabled must be boolean")
        if self.acceptance_control_enabled and self.acceptance_registry_dir is None:
            raise ValueError("controlled acceptance requires a host-configured registry directory")
        if not self.acceptance_control_enabled and (self.acceptance_registry_dir or self.acceptance_lease_id):
            raise ValueError("acceptance settings require SHORTDRAMA_ACCEPTANCE_CONTROL=1; refusing silent fallback")

    def validate(self) -> None:
        if type(self.member_connection_access) is not bool:
            raise ValueError("member_connection_access must be boolean")
        if self.member_connection_access and self.auth_mode != "oauth":
            raise ValueError("member connections require OAuth")
        if not isinstance(self.admin_subjects, tuple) or any(
            not isinstance(subject, str) or not subject.strip() or len(subject) > 512
            for subject in self.admin_subjects
        ):
            raise ValueError("SHORTDRAMA_ADMIN_SUBJECTS must be a JSON array of OAuth subject strings")
        self.validate_acceptance_control()
        if self.deployment_mode not in {"local", "cloud"}:
            raise ValueError("SHORTDRAMA_DEPLOYMENT_MODE must be local or cloud")
        if self.auth_mode not in {"none", "oauth"}:
            raise ValueError("SHORTDRAMA_AUTH_MODE must be none or oauth")
        parsed = urlparse(self.public_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("PUBLIC_BASE_URL must be an absolute HTTP(S) URL")
        if self.deployment_mode == "cloud":
            if parsed.scheme != "https":
                raise ValueError("cloud mode requires an HTTPS PUBLIC_BASE_URL")
            if self.auth_mode != "oauth":
                raise ValueError("cloud mode refuses unauthenticated private project writes; configure OAuth")
        if self.auth_mode == "oauth":
            missing = [
                name
                for name, value in {
                    "OAUTH_ISSUER": self.oauth_issuer,
                    "OAUTH_AUDIENCE": self.oauth_audience,
                    "OAUTH_JWKS_URL": self.oauth_jwks_url,
                }.items()
                if not value
            ]
            if missing:
                raise ValueError(f"OAuth mode is missing: {', '.join(missing)}")
            for name, value in {
                "OAUTH_ISSUER": self.oauth_issuer,
                "OAUTH_JWKS_URL": self.oauth_jwks_url,
            }.items():
                parsed_oauth = urlparse(str(value))
                if parsed_oauth.scheme not in {"http", "https"} or not parsed_oauth.netloc:
                    raise ValueError(f"{name} must be an absolute HTTP(S) URL")
                if self.deployment_mode == "cloud" and parsed_oauth.scheme != "https":
                    raise ValueError(f"cloud mode requires HTTPS for {name}")
