import os
import sys
import types

# The module creates a boto3 client at import: give it a region and dummy credentials so tests never read ~/.aws.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
try:
    import boto3  # noqa: F401
except ImportError:
    m = types.ModuleType("boto3")
    m.client = lambda *a, **k: None
    sys.modules["boto3"] = m
