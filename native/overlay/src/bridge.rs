use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use std::io::ErrorKind;
use thiserror::Error;
use tokio::net::TcpStream;
use tokio_tungstenite::{connect_async, tungstenite::Message, MaybeTlsStream, WebSocketStream};

use crate::logging::OverlayLoggingMode;
use crate::manifest::OverlayManifest;
use crate::state::OverlayPresentationSnapshot;

#[derive(Debug, Clone, PartialEq)]
pub enum OverlayBridgeEvent {
    Shutdown,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OverlayRuntimeControl {
    pub logging_mode: OverlayLoggingMode,
}

#[derive(Debug, Clone, PartialEq)]
pub enum BridgeIncoming {
    Snapshot(OverlayPresentationSnapshot),
    Heartbeat,
    Event(OverlayBridgeEvent),
    Control(OverlayRuntimeControl),
}

#[derive(Debug, Error)]
pub enum BridgeError {
    #[error("bridge connect failed: {0}")]
    Connect(String),
    #[error("bridge auth failed: {0}")]
    Auth(String),
    #[error("bridge protocol error: {0}")]
    Protocol(String),
    #[error("bridge disconnected")]
    Disconnected,
}

pub struct BridgeClient {
    stream: WebSocketStream<MaybeTlsStream<TcpStream>>,
}

impl BridgeClient {
    pub async fn connect(
        manifest: &OverlayManifest,
    ) -> Result<(Self, OverlayPresentationSnapshot), BridgeError> {
        let (mut stream, _response) = connect_async(&manifest.bridge_url)
            .await
            .map_err(|error| BridgeError::Connect(error.to_string()))?;

        let auth_payload = json!({
            "type": "auth",
            "session_token": manifest.session_token,
        });
        stream
            .send(Message::Text(auth_payload.to_string().into()))
            .await
            .map_err(|error| BridgeError::Connect(error.to_string()))?;

        let snapshot = match Self::read_json_message(&mut stream).await? {
            Value::Object(payload) => match payload.get("type").and_then(Value::as_str) {
                Some("auth_error") => {
                    return Err(BridgeError::Auth("bridge rejected session token".into()))
                }
                Some("snapshot") => {
                    let snapshot_value = payload
                        .get("payload")
                        .cloned()
                        .ok_or_else(|| BridgeError::Protocol("snapshot payload missing".into()))?;
                    serde_json::from_value(snapshot_value)
                        .map_err(|error| BridgeError::Protocol(error.to_string()))?
                }
                other => {
                    return Err(BridgeError::Protocol(format!(
                        "expected snapshot after auth, received {other:?}"
                    )))
                }
            },
            _ => {
                return Err(BridgeError::Protocol(
                    "bridge payload must decode to an object".into(),
                ))
            }
        };

        Ok((Self { stream }, snapshot))
    }

    pub async fn send_json(&mut self, payload: Value) -> Result<(), BridgeError> {
        self.stream
            .send(Message::Text(payload.to_string().into()))
            .await
            .map_err(|error| BridgeError::Connect(error.to_string()))
    }

    pub async fn close(&mut self) -> Result<(), BridgeError> {
        match self.stream.close(None).await {
            Ok(()) => Ok(()),
            Err(tokio_tungstenite::tungstenite::Error::ConnectionClosed)
            | Err(tokio_tungstenite::tungstenite::Error::AlreadyClosed) => Ok(()),
            Err(error) => Err(BridgeError::Connect(error.to_string())),
        }
    }

    pub async fn next_message(&mut self) -> Result<BridgeIncoming, BridgeError> {
        let payload = Self::read_json_message(&mut self.stream).await?;
        let Value::Object(map) = payload else {
            return Err(BridgeError::Protocol(
                "bridge payload must decode to an object".into(),
            ));
        };

        let event_type = map
            .get("type")
            .and_then(Value::as_str)
            .ok_or_else(|| BridgeError::Protocol("bridge payload is missing type".into()))?;

        match event_type {
            "snapshot" => {
                let snapshot_value = map
                    .get("payload")
                    .cloned()
                    .ok_or_else(|| BridgeError::Protocol("snapshot payload missing".into()))?;
                let snapshot = serde_json::from_value(snapshot_value)
                    .map_err(|error| BridgeError::Protocol(error.to_string()))?;
                Ok(BridgeIncoming::Snapshot(snapshot))
            }
            "heartbeat" => Ok(BridgeIncoming::Heartbeat),
            "auth_error" => Err(BridgeError::Auth("bridge rejected session token".into())),
            "shutdown" => Ok(BridgeIncoming::Event(OverlayBridgeEvent::Shutdown)),
            "runtime_control" => {
                let payload = map.get("payload").cloned().ok_or_else(|| {
                    BridgeError::Protocol("runtime_control payload missing".into())
                })?;
                let payload_map = payload.as_object().ok_or_else(|| {
                    BridgeError::Protocol("runtime_control payload must be an object".into())
                })?;
                let logging_mode = payload_map.get("logging_mode").cloned().ok_or_else(|| {
                    BridgeError::Protocol("runtime_control logging_mode missing".into())
                })?;
                let logging_mode = serde_json::from_value(logging_mode)
                    .map_err(|error| BridgeError::Protocol(error.to_string()))?;
                Ok(BridgeIncoming::Control(OverlayRuntimeControl {
                    logging_mode,
                }))
            }
            _ => Err(BridgeError::Protocol(format!(
                "unsupported bridge payload type: {event_type}"
            ))),
        }
    }

    async fn read_json_message(
        stream: &mut WebSocketStream<MaybeTlsStream<TcpStream>>,
    ) -> Result<Value, BridgeError> {
        loop {
            let next_item = stream.next().await.ok_or(BridgeError::Disconnected)?;
            let message = next_item.map_err(bridge_error_from_read_error)?;
            match message {
                Message::Text(text) => {
                    let payload = serde_json::from_str::<Value>(&text)
                        .map_err(|error| BridgeError::Protocol(error.to_string()))?;
                    return Ok(payload);
                }
                Message::Binary(_) => {
                    return Err(BridgeError::Protocol(
                        "bridge payload must be text JSON".into(),
                    ))
                }
                Message::Close(_) => return Err(BridgeError::Disconnected),
                Message::Ping(_) | Message::Pong(_) | Message::Frame(_) => continue,
            }
        }
    }
}

fn bridge_error_from_read_error(error: tokio_tungstenite::tungstenite::Error) -> BridgeError {
    match error {
        tokio_tungstenite::tungstenite::Error::ConnectionClosed
        | tokio_tungstenite::tungstenite::Error::AlreadyClosed => BridgeError::Disconnected,
        tokio_tungstenite::tungstenite::Error::Io(io_error)
            if matches!(
                io_error.kind(),
                ErrorKind::BrokenPipe
                    | ErrorKind::ConnectionAborted
                    | ErrorKind::ConnectionReset
                    | ErrorKind::NotConnected
                    | ErrorKind::UnexpectedEof
            ) =>
        {
            BridgeError::Disconnected
        }
        other => BridgeError::Protocol(other.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn read_side_websocket_errors_map_to_disconnected() {
        let error = tokio_tungstenite::tungstenite::Error::ConnectionClosed;
        assert!(matches!(
            bridge_error_from_read_error(error),
            BridgeError::Disconnected
        ));
    }

    #[test]
    fn read_side_protocol_errors_remain_protocol_failures() {
        let error = tokio_tungstenite::tungstenite::Error::Protocol(
            tokio_tungstenite::tungstenite::error::ProtocolError::ResetWithoutClosingHandshake,
        );
        assert!(matches!(
            bridge_error_from_read_error(error),
            BridgeError::Protocol(_)
        ));
    }
}
