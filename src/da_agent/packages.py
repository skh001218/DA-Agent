"""Integrity checked, version pinned training packages (no public answer fields)."""
from dataclasses import dataclass
from pathlib import Path
import csv
import hashlib
import json
import re


class PackageError(ValueError):
    pass


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_file(base, name):
    candidate = (base / name).resolve()
    if not candidate.is_relative_to(base.resolve()) or not candidate.is_file():
        raise PackageError("Missing or unsafe package file")
    return candidate


@dataclass
class Package:
    path: Path
    public: dict
    private: dict

    @property
    def schema_name(self):
        key = self.public["package_id"] + "/" + self.public["release_version"]
        return "pkg_" + hashlib.sha256(key.encode()).hexdigest()[:20]

    def problem(self, problem_id):
        for problem in self.public["problems"]:
            if problem["problem_id"] == problem_id:
                return read_json(safe_file(self.path / "public", problem["path"]))
        raise PackageError("Unknown problem")

    def reference(self, problem_id):
        for ref in self.private["problems"]:
            if ref["problem_id"] == problem_id:
                return read_json(safe_file(self.path / "private", ref["path"]))
        raise PackageError("Missing reference")

    def rows(self, table):
        entry = next(x for x in self.public["data_files"] if x["table"] == table)
        with safe_file(self.path / "public", entry["path"]).open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))


class PackageCatalog:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def load(self, package_id, release_version, *, allow_unvalidated=False):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", package_id) or not re.fullmatch(r"v[1-9][0-9]*", release_version):
            raise PackageError("Invalid package identity")
        path = (self.root / package_id / release_version).resolve()
        if not path.is_relative_to(self.root):
            raise PackageError("Unsafe release path")
        try:
            public = read_json(path / "public/manifest.json")
            private = read_json(path / "private/manifest.json")
            for manifest in (public, private):
                if manifest["package_id"] != package_id or manifest["release_version"] != release_version or manifest["dataset_version"] != release_version:
                    raise PackageError("Package identity/version mismatch")
            if public["dataset_id"] != private["dataset_id"]:
                raise PackageError("Dataset identity mismatch")
            public_ids = [p["problem_id"] for p in public["problems"]]
            if not public_ids or len(public_ids) != len(set(public_ids)) or set(public_ids) != {p["problem_id"] for p in private["problems"]}:
                raise PackageError("Missing or duplicate reference")
            for section, entries, version_key in (("public", public["data_files"], None), ("public", public["problems"], "problem_version"), ("private", private["problems"], "evaluation_version")):
                for entry in entries:
                    file = safe_file(path / section, entry["path"])
                    if digest(file) != entry["sha256"]:
                        raise PackageError("Package content modified")
                    if version_key:
                        content = read_json(file)
                        if entry[version_key] != release_version or content[version_key] != release_version or content["package_id"] != package_id or content["release_version"] != release_version or content["dataset_id"] != public["dataset_id"] or content["problem_id"] != entry["problem_id"]:
                            raise PackageError("Problem/reference identity mismatch")
            package = Package(path, public, private)
            if not allow_unvalidated:
                receipt = read_json(path / "private/validation.json")
                if receipt.get("status") != "publishable" or receipt.get("public_sha256") != digest(path / "public/manifest.json") or receipt.get("private_sha256") != digest(path / "private/manifest.json"):
                    raise PackageError("Package not validated")
            return package
        except (OSError, KeyError, StopIteration, json.JSONDecodeError) as exc:
            raise PackageError("Incomplete or invalid package") from exc

    def list_public(self):
        result = []
        for file in sorted(self.root.glob("*/v*/public/manifest.json")):
            try:
                result.append(self.load(file.parents[2].name, file.parents[1].name).public)
            except PackageError:
                continue
        return result
