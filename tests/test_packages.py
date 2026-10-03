import json
import pytest

from da_agent.data import generate_package, validate_rows
from da_agent.packages import PackageCatalog, PackageError, digest, write_json


def test_deterministic_generation(tmp_path):
    a = generate_package(tmp_path / "a")
    b = generate_package(tmp_path / "b")
    assert a.public == b.public
    assert a.private == b.private
    assert validate_rows(a) == validate_rows(b)
    with pytest.raises(FileExistsError):
        generate_package(tmp_path / "a")


def test_pending_is_not_listed(tmp_path):
    generate_package(tmp_path)
    assert PackageCatalog(tmp_path).list_public() == []
    with pytest.raises(PackageError):
        PackageCatalog(tmp_path).load("training-001", "v1")


def test_public_does_not_contain_private_material(tmp_path):
    package = generate_package(tmp_path)
    serialized = json.dumps(package.public) + json.dumps(package.problem("problem-001"))
    for token in ["seed", "expected", "reference", "private", "churn_rate", "hints"]:
        assert token not in serialized
    with pytest.raises(PackageError):
        package.problem("missing")


@pytest.mark.parametrize("target", ["public/users.csv", "public/problem-001.json", "private/reference-001.json"])
def test_modified_content_is_rejected(tmp_path, target):
    package = generate_package(tmp_path)
    with (package.path / target).open("a", encoding="utf-8") as f:
        f.write(" ")
    with pytest.raises(PackageError):
        PackageCatalog(tmp_path).load("training-001", "v1", allow_unvalidated=True)


@pytest.mark.parametrize("field,value", [("package_id", "another"), ("release_version", "v2"), ("dataset_version", "v2"), ("dataset_id", "another")])
def test_mixed_manifest_is_rejected(tmp_path, field, value):
    package = generate_package(tmp_path)
    manifest = dict(package.private)
    manifest[field] = value
    write_json(package.path / "private/manifest.json", manifest)
    with pytest.raises(PackageError):
        PackageCatalog(tmp_path).load("training-001", "v1", allow_unvalidated=True)


def test_missing_reference_and_escape_rejected(tmp_path):
    package = generate_package(tmp_path)
    package.private["problems"] = []
    write_json(package.path / "private/manifest.json", package.private)
    with pytest.raises(PackageError):
        PackageCatalog(tmp_path).load("training-001", "v1", allow_unvalidated=True)
    package.public["data_files"][0]["path"] = "../../outside.csv"
    write_json(package.path / "public/manifest.json", package.public)
    with pytest.raises(PackageError):
        PackageCatalog(tmp_path).load("training-001", "v1", allow_unvalidated=True)


def test_release_pinning(tmp_path):
    v1 = generate_package(tmp_path)
    v2 = generate_package(tmp_path, release_version="v2", seed=8)
    catalog = PackageCatalog(tmp_path)
    assert catalog.load("training-001", "v1", allow_unvalidated=True).public == v1.public
    assert catalog.load("training-001", "v2", allow_unvalidated=True).public == v2.public
    assert v1.schema_name != v2.schema_name
    with pytest.raises(PackageError):
        catalog.load("../training-001", "v1")


def test_reference_id_checked_even_with_new_hash(tmp_path):
    package = generate_package(tmp_path)
    ref = package.reference("problem-001")
    ref["package_id"] = "another"
    file = package.path / "private/reference-001.json"
    write_json(file, ref)
    package.private["problems"][0]["sha256"] = digest(file)
    write_json(package.path / "private/manifest.json", package.private)
    with pytest.raises(PackageError):
        PackageCatalog(tmp_path).load("training-001", "v1", allow_unvalidated=True)
