"""Wires configuration, repositories and clients for a Lambda invocation."""
import os
from dataclasses import dataclass, replace

import boto3

from .clients.action_gateway import ActionGateway
from .clients.read_gateway import ReadGateway
from .clients.reasoning_runtime import ReasoningRuntime
from .clients.ses_outbound import SesOutbound
from .config import Config
from .repositories import Repositories


@dataclass
class Services:
    cfg: Config
    repos: Repositories
    reasoning: ReasoningRuntime
    ses: SesOutbound
    s3: object = None
    read: object = None       # ReadGateway: pre-action checks and verification
    action: object = None     # ActionGateway: the only WRITE path
    sfn: object = None        # Step Functions client: starts the verification workflow


def load_pepper(cfg, env=None, ssm=None):
    """Pepper comes from an SSM SecureString (APPROVAL_TOKEN_PEPPER_PARAM); env is a local-dev fallback."""
    env = os.environ if env is None else env
    param = env.get("APPROVAL_TOKEN_PEPPER_PARAM")
    if param:
        ssm = ssm or boto3.client("ssm", region_name=cfg.region)
        return ssm.get_parameter(Name=param, WithDecryption=True)["Parameter"]["Value"]
    return cfg.token_pepper


def build_services():
    cfg = Config.from_env()
    cfg = replace(cfg, token_pepper=load_pepper(cfg))
    return Services(
        cfg, Repositories.from_config(cfg), ReasoningRuntime(cfg.reasoning_runtime_arn, cfg.region), SesOutbound(cfg),
        s3=boto3.client("s3", region_name=cfg.region),
        read=ReadGateway(cfg.read_gateway_url, region=cfg.region) if cfg.read_gateway_url else None,
        action=ActionGateway(cfg.action_gateway_url, region=cfg.region) if cfg.action_gateway_url else None,
        sfn=boto3.client("stepfunctions", region_name=cfg.region))
