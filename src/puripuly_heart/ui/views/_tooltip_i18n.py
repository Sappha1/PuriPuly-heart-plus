"""Runtime translations for the settings ``ⓘ`` tooltips.

These tooltips were originally hardcoded English literals passed to
``SettingsView._info_title(...)``, so they stayed English in every locale. Rather
than convert ~29 call sites to i18n keys (and risk exact-match edits against
literals that contain a corrupted em-dash byte shown as ``�``), we translate them
here by matching a unique ASCII *prefix* of each English literal. The prefix is
always the portion before any non-ASCII/garbled character, so matching is robust
regardless of how the original literal's dash byte decodes.

``translate_tooltip()`` is called both at build time (so the first paint is already
localized) and from ``apply_locale()`` (so a runtime language switch updates them
without an app restart). For an unknown string (e.g. a tooltip that already came
from ``t(...)``) it returns the input unchanged.
"""

from __future__ import annotations

# Each entry: ASCII prefix -> {locale: translation}. The "en" value is a CLEANED
# version of the original literal (proper em-dashes) so even English renders nicely.
_TOOLTIP_TABLE: tuple[tuple[str, dict[str, str]], ...] = (
    (
        "Provides the AI translator with extra context",
        {
            "en": "Provides the AI translator with extra context about the ongoing conversation to improve accuracy and naturalness.",
            "zh-CN": "为 AI 翻译器提供有关当前对话的额外上下文，以提高准确性和自然度。",
            "ja": "進行中の会話に関する追加のコンテキストをAI翻訳に提供し、精度と自然さを向上させます。",
            "ko": "진행 중인 대화에 대한 추가 컨텍스트를 AI 번역기에 제공하여 정확성과 자연스러움을 높입니다.",
        },
    ),
    (
        "Which speech recognition engine listens",
        {
            "en": "Which speech recognition engine listens to your microphone and converts your speech to text.",
            "zh-CN": "用于聆听您的麦克风并将语音转换为文字的语音识别引擎。",
            "ja": "マイクを聞き取り、音声をテキストに変換する音声認識エンジンです。",
            "ko": "마이크를 듣고 음성을 텍스트로 변환하는 음성 인식 엔진입니다.",
        },
    ),
    (
        "Which AI model or service translates",
        {
            "en": "Which AI model or service translates text between languages.",
            "zh-CN": "用于在不同语言之间翻译文本的 AI 模型或服务。",
            "ja": "言語間でテキストを翻訳するAIモデルまたはサービスです。",
            "ko": "언어 간 텍스트를 번역하는 AI 모델 또는 서비스입니다.",
        },
    ),
    (
        "The display language for this app",
        {
            "en": "The display language for this app's menus and labels.",
            "zh-CN": "本应用菜单和标签的显示语言。",
            "ja": "このアプリのメニューとラベルの表示言語です。",
            "ko": "이 앱의 메뉴와 라벨에 표시되는 언어입니다.",
        },
    ),
    (
        "When ON, also sends your original",
        {
            "en": "When ON, also sends your original (untranslated) speech to the VRChat chatbox alongside the translation.",
            "zh-CN": "开启后，除译文外还会将您的原始（未翻译）语音一并发送到 VRChat 聊天框。",
            "ja": "オンにすると、翻訳に加えて元の（未翻訳の）音声もVRChatのチャットボックスに送信します。",
            "ko": "켜면 번역문과 함께 원본(번역되지 않은) 음성도 VRChat 채팅창에 보냅니다.",
        },
    ),
    (
        "When ON, any text you copy",
        {
            "en": "When ON, any text you copy to your clipboard is automatically translated and sent to the VRChat chatbox.",
            "zh-CN": "开启后，您复制到剪贴板的任何文本都会被自动翻译并发送到 VRChat 聊天框。",
            "ja": "オンにすると、クリップボードにコピーしたテキストが自動的に翻訳され、VRChatのチャットボックスに送信されます。",
            "ko": "켜면 클립보드에 복사한 텍스트가 자동으로 번역되어 VRChat 채팅창에 전송됩니다.",
        },
    ),
    (
        "Sync mic mute state with VRChat",
        {
            "en": "Sync mic mute state with VRChat — suppresses your microphone input to the app while you are muted in VRChat.",
            "zh-CN": "将麦克风静音状态与 VRChat 同步——当您在 VRChat 中静音时，暂停向本应用输入麦克风音频。",
            "ja": "マイクのミュート状態をVRChatと同期します。VRChatでミュート中はアプリへのマイク入力を停止します。",
            "ko": "마이크 음소거 상태를 VRChat과 동기화합니다. VRChat에서 음소거된 동안 앱으로의 마이크 입력을 중단합니다.",
        },
    ),
    (
        "Record a short clip from your microphone",
        {
            "en": "Record a short clip from your microphone to verify it is being picked up correctly before starting a session.",
            "zh-CN": "录制一小段麦克风音频，在开始会话前确认麦克风能被正确拾取。",
            "ja": "セッションを開始する前に、マイクが正しく認識されているか確認するために短いクリップを録音します。",
            "ko": "세션을 시작하기 전에 마이크가 올바르게 인식되는지 확인하기 위해 짧은 클립을 녹음합니다.",
        },
    ),
    (
        "The audio driver type used to access",
        {
            "en": "The audio driver type used to access your microphone (e.g. WASAPI, MME). Try changing this if your microphone is not detected.",
            "zh-CN": "用于访问麦克风的音频驱动类型（如 WASAPI、MME）。如果检测不到麦克风，可尝试更改此项。",
            "ja": "マイクへのアクセスに使用するオーディオドライバーの種類（WASAPI、MMEなど）。マイクが検出されない場合は変更してみてください。",
            "ko": "마이크에 접근하는 데 사용하는 오디오 드라이버 종류(WASAPI, MME 등)입니다. 마이크가 감지되지 않으면 변경해 보세요.",
        },
    ),
    (
        "The specific microphone input device",
        {
            "en": "The specific microphone input device PuriPuly listens to for your speech.",
            "zh-CN": "PuriPuly 用来聆听您语音的具体麦克风输入设备。",
            "ja": "PuriPulyがあなたの音声を聞き取る特定のマイク入力デバイスです。",
            "ko": "PuriPuly가 음성을 듣는 특정 마이크 입력 장치입니다.",
        },
    ),
    (
        "The audio output device to capture for peer translation. Usually your headset output or speakers",
        {
            "en": "The audio output device to capture for peer translation. Usually your headset output or speakers — what you hear.",
            "zh-CN": "用于捕获对方语音以进行翻译的音频输出设备。通常是您的耳机或扬声器输出——即您所听到的声音。",
            "ja": "相手の翻訳のためにキャプチャするオーディオ出力デバイスです。通常はヘッドセットやスピーカーの出力（あなたが聞いている音）です。",
            "ko": "상대방 번역을 위해 캡처할 오디오 출력 장치입니다. 보통 헤드셋 출력이나 스피커(당신이 듣는 소리)입니다.",
        },
    ),
    (
        "When ON, prioritises speed over thoroughness",
        {
            "en": "When ON, prioritises speed over thoroughness — translations arrive faster but may be slightly less accurate or natural.",
            "zh-CN": "开启后，优先考虑速度而非精确度——译文到达更快，但可能略微不够准确或自然。",
            "ja": "オンにすると、精度よりも速度を優先します。翻訳は速く届きますが、正確さや自然さがやや劣る場合があります。",
            "ko": "켜면 정확성보다 속도를 우선합니다. 번역이 더 빨리 도착하지만 정확성이나 자연스러움이 약간 떨어질 수 있습니다.",
        },
    ),
    (
        "How sensitive the voice detector is to your",
        {
            "en": "How sensitive the voice detector is to your microphone. Higher = triggers more easily; lower = requires louder speech.",
            "zh-CN": "语音检测器对您麦克风的灵敏度。数值越高越容易触发；越低则需要更大的声音。",
            "ja": "音声検出器のマイクに対する感度です。高いほど反応しやすく、低いほど大きな声が必要です。",
            "ko": "음성 감지기가 마이크에 반응하는 민감도입니다. 높을수록 쉽게 작동하고, 낮을수록 더 큰 소리가 필요합니다.",
        },
    ),
    (
        "How sensitive the voice detector is to the peer",
        {
            "en": "How sensitive the voice detector is to the peer's audio. Adjust if their speech is being cut off or triggering on silence.",
            "zh-CN": "语音检测器对对方音频的灵敏度。如果对方的语音被截断或在静音时误触发，可调整此项。",
            "ja": "音声検出器の相手の音声に対する感度です。相手の発話が途切れる場合や無音で誤作動する場合は調整してください。",
            "ko": "음성 감지기가 상대방 오디오에 반응하는 민감도입니다. 상대방 음성이 끊기거나 무음에서 작동하면 조정하세요.",
        },
    ),
    (
        "Which speech recognition engine transcribes",
        {
            "en": "Which speech recognition engine transcribes the other person's speech (captured via loopback audio from your headset/speakers).",
            "zh-CN": "用于转写对方语音的语音识别引擎（通过耳机/扬声器的环回音频捕获）。",
            "ja": "相手の音声を文字起こしする音声認識エンジンです（ヘッドセット／スピーカーのループバック音声で取得）。",
            "ko": "상대방 음성을 받아 적는 음성 인식 엔진입니다(헤드셋/스피커의 루프백 오디오로 캡처).",
        },
    ),
    (
        "Show the translated version",
        {
            "en": "Show the translated version of what the other person says in the overlay/caption window.",
            "zh-CN": "在叠加层/字幕窗口中显示对方所说内容的译文。",
            "ja": "相手の発言の翻訳をオーバーレイ／字幕ウィンドウに表示します。",
            "ko": "상대방이 말한 내용의 번역을 오버레이/자막 창에 표시합니다.",
        },
    ),
    (
        "Show the other person's original",
        {
            "en": "Show the other person's original (untranslated) speech in the overlay alongside or instead of the translation.",
            "zh-CN": "在叠加层中显示对方的原始（未翻译）语音，与译文一并显示或替代译文。",
            "ja": "相手の元の（未翻訳の）発言を、翻訳と並べて、または翻訳の代わりにオーバーレイに表示します。",
            "ko": "상대방의 원본(번역되지 않은) 음성을 번역과 함께 또는 대신 오버레이에 표시합니다.",
        },
    ),
    (
        "When ON, your own translated messages",
        {
            "en": "When ON, your own translated messages also appear in the overlay, not just the other person's responses.",
            "zh-CN": "开启后，您自己的译文消息也会显示在叠加层中，而不仅仅是对方的回复。",
            "ja": "オンにすると、相手の返答だけでなく、あなた自身の翻訳メッセージもオーバーレイに表示されます。",
            "ko": "켜면 상대방의 응답뿐 아니라 당신의 번역 메시지도 오버레이에 표시됩니다.",
        },
    ),
    (
        "When ON, only the most recent message",
        {
            "en": "When ON, only the most recent message is shown in the overlay instead of a scrolling history of the conversation.",
            "zh-CN": "开启后，叠加层中只显示最新的一条消息，而非滚动的对话历史。",
            "ja": "オンにすると、会話のスクロール履歴ではなく、最新のメッセージのみがオーバーレイに表示されます。",
            "ko": "켜면 대화의 스크롤 기록 대신 가장 최근 메시지만 오버레이에 표시됩니다.",
        },
    ),
    (
        "Where captions appear",
        {
            "en": "Where captions appear — as a SteamVR overlay panel (visible in VR headset) or as a floating desktop window.",
            "zh-CN": "字幕显示的位置——作为 SteamVR 叠加面板（在 VR 头显中可见）或作为浮动桌面窗口。",
            "ja": "字幕の表示場所です。SteamVRオーバーレイパネル（VRヘッドセットで表示）またはフローティングデスクトップウィンドウとして表示します。",
            "ko": "자막이 표시되는 위치입니다. SteamVR 오버레이 패널(VR 헤드셋에서 보임) 또는 떠 있는 데스크톱 창으로 표시합니다.",
        },
    ),
    (
        "The point in VR space where the overlay panel is anchored",
        {
            "en": "The point in VR space where the overlay panel is anchored — e.g. head, left wrist, or world position.",
            "zh-CN": "叠加面板在 VR 空间中的锚定位置——例如头部、左手腕或世界坐标。",
            "ja": "オーバーレイパネルがVR空間で固定される位置です。例：頭、左手首、ワールド座標など。",
            "ko": "오버레이 패널이 VR 공간에서 고정되는 지점입니다. 예: 머리, 왼쪽 손목, 월드 위치 등.",
        },
    ),
    (
        "The text size preset in the overlay",
        {
            "en": "The text size preset in the overlay — Large, Normal, or Small.",
            "zh-CN": "叠加层中的文字大小预设——大、正常或小。",
            "ja": "オーバーレイの文字サイズプリセットです。大、標準、小から選べます。",
            "ko": "오버레이의 텍스트 크기 프리셋입니다. 크게, 보통, 작게 중에서 선택합니다.",
        },
    ),
    (
        "Move the VR overlay back",
        {
            "en": "Move the VR overlay back to its default position if it has drifted or ended up out of view in your headset.",
            "zh-CN": "如果 VR 叠加层发生漂移或移出头显视野，可将其移回默认位置。",
            "ja": "VRオーバーレイがずれたり、ヘッドセットの視界から外れた場合に、既定の位置に戻します。",
            "ko": "VR 오버레이가 밀리거나 헤드셋 시야에서 벗어난 경우 기본 위치로 되돌립니다.",
        },
    ),
    (
        "Move the floating desktop overlay window back",
        {
            "en": "Move the floating desktop overlay window back to its default screen position if it has been dragged off screen.",
            "zh-CN": "如果浮动桌面叠加窗口被拖出屏幕，可将其移回默认屏幕位置。",
            "ja": "フローティングデスクトップオーバーレイウィンドウが画面外にドラッグされた場合に、既定の画面位置に戻します。",
            "ko": "떠 있는 데스크톱 오버레이 창이 화면 밖으로 드래그된 경우 기본 화면 위치로 되돌립니다.",
        },
    ),
    (
        "The size preset for the floating desktop caption window",
        {
            "en": "The size preset for the floating desktop caption window — Small, Medium, or Large.",
            "zh-CN": "浮动桌面字幕窗口的尺寸预设——小、中或大。",
            "ja": "フローティングデスクトップ字幕ウィンドウのサイズプリセットです。小、中、大から選べます。",
            "ko": "떠 있는 데스크톱 자막 창의 크기 프리셋입니다. 작게, 보통, 크게 중에서 선택합니다.",
        },
    ),
    (
        "How transparent the desktop overlay",
        {
            "en": "How transparent the desktop overlay background is. 0% is fully invisible, 100% is a solid opaque background.",
            "zh-CN": "桌面叠加层背景的透明度。0% 为完全不可见，100% 为完全不透明的纯色背景。",
            "ja": "デスクトップオーバーレイ背景の透明度です。0%は完全に透明、100%は不透明な背景になります。",
            "ko": "데스크톱 오버레이 배경의 투명도입니다. 0%는 완전히 투명, 100%는 불투명한 배경입니다.",
        },
    ),
    (
        "Lock the desktop overlay in place",
        {
            "en": "Lock the desktop overlay in place so it cannot be accidentally moved by clicking and dragging.",
            "zh-CN": "将桌面叠加层锁定到位，以免在点击和拖动时被意外移动。",
            "ja": "デスクトップオーバーレイを固定し、クリックやドラッグで誤って動かないようにします。",
            "ko": "데스크톱 오버레이를 제자리에 고정하여 클릭과 드래그로 실수로 움직이지 않도록 합니다.",
        },
    ),
    (
        "How PuriPuly connects to AI translation services",
        {
            "en": "How PuriPuly connects to AI translation services — Managed (no setup needed), OpenRouter (your own key), or direct API keys.",
            "zh-CN": "PuriPuly 连接 AI 翻译服务的方式——托管（无需设置）、OpenRouter（使用您自己的密钥）或直接 API 密钥。",
            "ja": "PuriPulyがAI翻訳サービスに接続する方法です。マネージド（設定不要）、OpenRouter（自分のキー）、または直接APIキー。",
            "ko": "PuriPuly가 AI 번역 서비스에 연결하는 방식입니다. 매니지드(설정 불필요), OpenRouter(자신의 키), 또는 직접 API 키.",
        },
    ),
    (
        "Which AI model OpenRouter uses",
        {
            "en": "Which AI model OpenRouter uses as a backup when the primary model is unavailable or rate-limited.",
            "zh-CN": "当主模型不可用或受到速率限制时，OpenRouter 用作备用的 AI 模型。",
            "ja": "プライマリモデルが利用できない、またはレート制限された場合に、OpenRouterがバックアップとして使用するAIモデルです。",
            "ko": "기본 모델을 사용할 수 없거나 속도 제한이 걸렸을 때 OpenRouter가 백업으로 사용하는 AI 모델입니다.",
        },
    ),
)


def translate_tooltip(tip: str, locale: str | None) -> str:
    """Return the localized settings tooltip for ``tip`` in ``locale``.

    Matches ``tip`` against a known ASCII prefix. Unknown strings (including
    tooltips that already came from ``t(...)``) are returned unchanged.
    """
    if not tip:
        return tip
    for prefix, translations in _TOOLTIP_TABLE:
        if tip.startswith(prefix):
            if locale and locale in translations:
                return translations[locale]
            if locale:
                base = locale.split("-")[0]
                for code, text in translations.items():
                    if code.split("-")[0] == base:
                        return text
            return translations.get("en", tip)
    return tip
