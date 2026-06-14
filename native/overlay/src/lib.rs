pub mod bridge;
pub mod logging;
pub mod manifest;
pub mod openvr;
pub mod renderer;
pub mod runtime;
pub mod state;

pub use bridge::{
    BridgeClient, BridgeError, BridgeIncoming, OverlayBridgeEvent, OverlayRuntimeControl,
};
pub use logging::{OverlayLogger, OverlayLoggingMode};
pub use manifest::{load_manifest, validate_manifest, OverlayManifest, EXPECTED_CONTRACT_VERSION};
pub use openvr::{
    submit_texture, FakeOpenVr, OpenVrError, OpenVrOverlay, OverlayFrameSubmitter,
    OverlayPlacementPolicy,
};
#[cfg(windows)]
pub use renderer::WindowsBundledFontCollection;
pub use renderer::{
    bundled_font_path_from_exe_dir, runtime_bundled_font_path, BlockBounds, BundledFaceId,
    CaptionBlock, CaptionBlockVariant, CaptionChannel, CaptionDebugOverlay, CaptionLayoutPolicy,
    CaptionLayoutResult, CaptionLineLayout, CaptionPresentation, CaptionRenderError,
    CaptionRenderer, DamageBand, FontFallbackReason, FontLanguageBucket, FontResolver, FontSource,
    FontWeight, RenderedFrame, ResolvedFontStyle, StyleBucketSourceCount, TextFamilyKey,
    TextLocaleKey, TextStyleDescriptor, TextStyleKey, VisibleCaptionBlock,
};
pub use runtime::{run_cli, run_with_manifest, OverlayRuntime, RuntimeFailure, StartupError};
pub use state::{
    OverlayCalibration, OverlayPresentationBlock, OverlayPresentationBlockVariant,
    OverlayPresentationCalibration, OverlayPresentationSnapshot, OverlayScene, OverlaySlot,
    OverlayState, OverlayStateSnapshot,
};
