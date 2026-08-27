import asyncio
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from wechatpy.enterprise import WeChatClient, parse_message
from wechatpy.enterprise.crypto import WeChatCrypto

from ..enums import ConnectorStatus
from .base import InboundMessage, OutboundTask


def _extract_encrypt(xml_text: str) -> str:
    return ET.fromstring(xml_text).findtext("Encrypt", "")


class WeComConnector:
    """企业微信连接器,双模式:

    - app 模式(推荐):自建应用回调收消息(crypto 解密)+ 主动接口发消息
    - webhook 模式(兼容旧配置):群机器人 webhook,仅出站
    """

    platform = "wecom"

    def __init__(self, connector_id: int, config: dict[str, Any]) -> None:
        self.connector_id = connector_id
        self.config = dict(config)
        self._status = ConnectorStatus.OFFLINE
        self._client: WeChatClient | None = None

    @property
    def _is_app(self) -> bool:
        return bool(self.config.get("corp_id") and self.config.get("corp_secret"))

    async def start(self, config: dict[str, Any] | None = None) -> None:
        if config:
            self.config.update(config)
        if self._is_app:
            self._client = WeChatClient(
                str(self.config["corp_id"]), str(self.config["corp_secret"])
            )
            self._status = ConnectorStatus.ONLINE
        elif self.config.get("webhook_url"):
            self._status = ConnectorStatus.ONLINE
        else:
            self._status = ConnectorStatus.OFFLINE

    async def stop(self) -> None:
        self._status = ConnectorStatus.OFFLINE

    async def status(self) -> ConnectorStatus:
        return self._status

    async def health(self) -> dict[str, Any]:
        return {
            "status": self._status.value,
            "platform": self.platform,
            "mode": "app" if self._is_app else "webhook",
            "configured": self._is_app or bool(self.config.get("webhook_url")),
        }

    def _crypto(self) -> WeChatCrypto:
        return WeChatCrypto(
            str(self.config.get("token", "")),
            str(self.config.get("encoding_aes_key", "")),
            str(self.config.get("corp_id", "")),
        )

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        return self._crypto().check_signature(msg_signature, timestamp, nonce, echostr)

    def parse_inbound(
        self, xml_body: str, msg_signature: str, timestamp: str, nonce: str
    ) -> InboundMessage | None:
        encrypted = _extract_encrypt(xml_body)
        decrypted = self._crypto().decrypt_message(encrypted, msg_signature, timestamp, nonce)
        msg = parse_message(decrypted)
        if msg.type != "text":
            return None
        return InboundMessage(
            connector_id=self.connector_id,
            sender_id=str(getattr(msg, "source", "")),
            text=str(getattr(msg, "content", "")),
            conversation_id=str(getattr(msg, "target", "")),
            external_message_id=str(getattr(msg, "id", "")),
        )

    async def send_task(self, task: OutboundTask) -> dict[str, Any]:
        if not self._is_app:
            return await self._send_webhook(task)
        if self._client is None:
            raise RuntimeError("企微应用客户端未初始化")
        target = task.target
        if not target:
            raise RuntimeError("企微发送需要 target(配置 chat_id 为运营者 userid)")
        body = await asyncio.to_thread(
            self._client.message.send_text,
            int(self.config.get("agent_id", 0)),
            [target],
            task.text,
        )
        return {"accepted": body.get("errcode", -1) == 0, "response": body}

    async def _send_webhook(self, task: OutboundTask) -> dict[str, Any]:
        webhook = str(self.config.get("webhook_url", ""))
        if not webhook:
            raise RuntimeError("WeCom 连接缺少 corp_id/corp_secret 或 webhook_url")
        payload: dict[str, Any] = {"msgtype": "text", "text": {"content": task.text}}
        if self.config.get("mentioned_list"):
            payload["text"]["mentioned_list"] = self.config["mentioned_list"]
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(webhook, json=payload)
            response.raise_for_status()
            body = response.json()
        return {"accepted": body.get("errcode", -1) == 0, "response": body}
