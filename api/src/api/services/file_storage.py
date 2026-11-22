from __future__ import annotations

from pathlib import Path
from typing import Protocol

import boto3
from botocore.exceptions import ClientError


class ObjectStorage(Protocol):
    def write(self, key: str, content: bytes) -> None: ...

    def read(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...


class LocalObjectStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, key: str, content: bytes) -> None:
        with (self.root / key).open("xb") as output:
            output.write(content)

    def read(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def delete(self, key: str) -> None:
        (self.root / key).unlink(missing_ok=True)


class S3ObjectStorage:
    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        endpoint_url: str | None,
        access_key: str | None,
        secret_key: str | None,
    ) -> None:
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        self._ensure_bucket(region)

    def _ensure_bucket(self, region: str) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
        parameters = {"Bucket": self.bucket}
        if region != "us-east-1":
            parameters["CreateBucketConfiguration"] = {"LocationConstraint": region}
        self.client.create_bucket(**parameters)

    def write(self, key: str, content: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content, IfNoneMatch="*")

    def read(self, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
                raise FileNotFoundError(key) from error
            raise
        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)
