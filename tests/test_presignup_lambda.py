"""Runs the inline pre sign-up Lambda from the CloudFormation template against a fake S3 client."""
import sys
import types
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


@pytest.fixture()
def fn(monkeypatch):
    from botocore.exceptions import ClientError

    tmpl = yaml.load((ROOT / "cloudformation/01-platform.yaml").read_text(), Loader=_Loader)
    code = tmpl["Resources"]["PreSignUpFunction"]["Properties"]["Code"]["ZipFile"]
    files = {k.name for k in (ROOT / "customer_policy").glob("*.txt")}

    class FakeS3:
        def head_object(self, Bucket, Key):
            if Key == "broken.txt":
                raise ClientError({"Error": {"Code": "403"}}, "HeadObject")
            if Key not in files:
                raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
            return {}

    monkeypatch.setenv("POLICY_BUCKET", "test-bucket")
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda *_: FakeS3()))
    module = types.ModuleType("presignup")
    exec(compile(code, "index.py", "exec"), module.__dict__)
    return module.handler


def event(username, source="PreSignUp_SignUp"):
    return {"triggerSource": source, "userName": username, "request": {}}


@pytest.mark.parametrize("username", ["jane_roe", "vinay.shinde", "new-user1"])
def test_any_new_username_allowed(fn, username):
    assert fn(event(username), None)["userName"] == username


@pytest.mark.parametrize("username", ["john_doe", "john_smith"])
def test_usernames_with_a_policy_are_reserved(fn, username):
    with pytest.raises(Exception, match="isn't available"):
        fn(event(username), None)


@pytest.mark.parametrize("username", ["ab", "John_Doe", "../x", "a b"])
def test_invalid_usernames_rejected(fn, username):
    with pytest.raises(Exception, match="Username must be"):
        fn(event(username), None)


def test_unexpected_s3_error_fails_closed(fn):
    from botocore.exceptions import ClientError
    with pytest.raises(ClientError):
        fn(event("broken"), None)


def test_admin_created_users_skip_check(fn):
    assert fn(event("john_doe", source="PreSignUp_AdminCreateUser"), None)
