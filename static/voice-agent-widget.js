/**
 * Agent Stream Voice Agent - Embeddable Web Component Widget (<agent-stream-voice>)
 * 
 * Usage:
 * <agent-stream-voice agent-id="agent_123" server-url="http://localhost:5000"></agent-stream-voice>
 * <script src="http://localhost:5000/static/voice-agent-widget.js" async></script>
 */

class VoiceAgentWidget extends HTMLElement {
    constructor() {
        super();
        this.attachShadow({ mode: 'open' });

        // State variables
        this.state = 'idle'; // idle, connecting, listening, thinking, speaking, muted, ended, error
        this.agentId = 'default';
        this.serverUrl = '';
        this.agentName = 'AI Voice Assistant';
        this.agentAvatar = '';
        this.ws = null;
        this.audioCtx = null;
        this.micStream = null;
        this.processor = null;
        this.source = null;
        this.isMuted = false;
        this.callStartTime = null;
        this.timerInterval = null;

        // Audio playback queue
        this.audioQueue = [];
        this.isPlayingAudio = false;
        this.nextStartTime = 0;
    }

    connectedCallback() {
        this.agentId = this.getAttribute('agent-id') || 'default';
        
        // Auto-detect server URL from script tag origin if server-url is not explicitly provided
        let defaultOrigin = window.location.origin;
        try {
            const scripts = document.querySelectorAll('script');
            for (let s of scripts) {
                if (s.src && s.src.includes('voice-agent-widget.js')) {
                    defaultOrigin = new URL(s.src).origin;
                    break;
                }
            }
        } catch (e) {}

        this.serverUrl = (this.getAttribute('server-url') || defaultOrigin).replace(/\/+$/, '');
        this.agentName = this.getAttribute('agent-name') || 'AI Voice Assistant';

        this.render();
        this.setupEventListeners();
        this.fetchAgentDetails();

        if (this.hasAttribute('auto-open') || this.getAttribute('mode') === 'iframe') {
            setTimeout(() => {
                const card = this.shadowRoot.getElementById('widget-card');
                if (card) card.classList.add('open');
            }, 100);
        }
    }

    disconnectedCallback() {
        this.endCall();
    }

    async fetchAgentDetails() {
        try {
            const res = await fetch(`${this.serverUrl}/api/exotel-sip/agents/${this.agentId}/public`);
            if (res.ok) {
                const data = await res.json();
                if (data.name) this.agentName = data.name;
                if (data.avatar_url) this.agentAvatar = data.avatar_url;
                
                const nameEl = this.shadowRoot.querySelector('.agent-name');
                if (nameEl) nameEl.textContent = this.agentName;
            }
        } catch (e) {
            console.log('VoiceAgentWidget: using default agent details');
        }
    }

    render() {
        const styles = `
            :host {
                --primary: #3b82f6;
                --primary-grad: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%);
                --bg: rgba(15, 23, 42, 0.92);
                --card-bg: rgba(30, 41, 59, 0.85);
                --border: rgba(255, 255, 255, 0.12);
                --text: #f8fafc;
                --text-muted: #94a3b8;
                --danger: #ef4444;
                --success: #10b981;
                font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                display: block !important;
                position: fixed !important;
                bottom: 24px !important;
                right: 24px !important;
                z-index: 2147483647 !important;
                box-sizing: border-box;
            }

            * {
                box-sizing: border-box;
                margin: 0;
                padding: 0;
            }

            /* Floating Launcher Button */
            .launcher-btn {
                width: 64px;
                height: 64px;
                border-radius: 50%;
                background: var(--primary-grad);
                border: 1px solid rgba(255, 255, 255, 0.2);
                box-shadow: 0 8px 32px rgba(59, 130, 246, 0.4);
                color: white;
                display: flex;
                align-items: center;
                justify-content: center;
                cursor: pointer;
                transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
                position: relative;
            }

            .launcher-btn:hover {
                transform: scale(1.08);
                box-shadow: 0 12px 40px rgba(59, 130, 246, 0.6);
            }

            .launcher-btn svg {
                width: 28px;
                height: 28px;
                fill: currentColor;
            }

            .pulse-ring {
                position: absolute;
                width: 100%;
                height: 100%;
                border-radius: 50%;
                border: 2px solid #3b82f6;
                opacity: 0;
                animation: pulse-ring 2s infinite;
            }

            @keyframes pulse-ring {
                0% { transform: scale(0.95); opacity: 0.8; }
                100% { transform: scale(1.6); opacity: 0; }
            }

            /* Main Card */
            .card {
                position: absolute;
                bottom: 80px;
                right: 0;
                width: 360px;
                background: var(--bg);
                backdrop-filter: blur(20px);
                -webkit-backdrop-filter: blur(20px);
                border: 1px solid var(--border);
                border-radius: 24px;
                box-shadow: 0 24px 48px rgba(0, 0, 0, 0.5);
                padding: 24px;
                display: flex;
                flex-direction: column;
                align-items: center;
                transform: scale(0.9) translateY(20px);
                opacity: 0;
                pointer-events: none;
                transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
                transform-origin: bottom right;
            }

            .card.open {
                transform: scale(1) translateY(0);
                opacity: 1;
                pointer-events: auto;
            }

            .header {
                width: 100%;
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
            }

            .brand-title {
                font-size: 0.75rem;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                color: var(--text-muted);
            }

            .close-btn {
                background: none;
                border: none;
                color: var(--text-muted);
                cursor: pointer;
                padding: 4px;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                transition: color 0.2s;
            }

            .close-btn:hover {
                color: var(--text);
            }

            /* Avatar & Status */
            .avatar-container {
                position: relative;
                margin-bottom: 16px;
            }

            .avatar {
                width: 88px;
                height: 88px;
                border-radius: 50%;
                background: var(--card-bg);
                border: 2px solid var(--border);
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 2.2rem;
                box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
                overflow: hidden;
            }

            .avatar img {
                width: 100%;
                height: 100%;
                object-fit: cover;
            }

            .status-ring {
                position: absolute;
                inset: -6px;
                border-radius: 50%;
                border: 2px dashed transparent;
                transition: all 0.3s ease;
            }

            .status-ring.listening {
                border-color: #3b82f6;
                animation: spin 8s linear infinite;
            }

            .status-ring.speaking {
                border-color: #10b981;
                animation: pulse-border 1.5s infinite alternate;
            }

            .status-ring.thinking {
                border-color: #8b5cf6;
                animation: spin 3s linear infinite;
            }

            @keyframes spin {
                100% { transform: rotate(360deg); }
            }

            .spin {
                animation: spin 1s linear infinite;
            }

            @keyframes pulse-border {
                0% { transform: scale(1); opacity: 0.6; }
                100% { transform: scale(1.06); opacity: 1; }
            }

            .agent-name {
                font-size: 1.25rem;
                font-weight: 700;
                color: var(--text);
                margin-bottom: 6px;
                text-align: center;
            }

            .status-badge {
                font-size: 0.8125rem;
                font-weight: 500;
                color: var(--text-muted);
                display: flex;
                align-items: center;
                gap: 6px;
                margin-bottom: 20px;
            }

            .dot {
                width: 8px;
                height: 8px;
                border-radius: 50%;
                background: var(--text-muted);
            }

            .dot.online { background: var(--success); }
            .dot.active { background: var(--primary); animation: blink 1s infinite alternate; }
            .dot.error { background: var(--danger); }

            @keyframes blink {
                0% { opacity: 0.3; }
                100% { opacity: 1; }
            }

            /* Visualizer Waves */
            .visualizer {
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 4px;
                height: 32px;
                margin-bottom: 24px;
            }

            .bar {
                width: 4px;
                height: 8px;
                background: var(--primary);
                border-radius: 2px;
                transition: height 0.15s ease;
            }

            .visualizer.active .bar {
                animation: wave 1.2s infinite ease-in-out;
            }

            .visualizer.active .bar:nth-child(1) { animation-delay: 0.0s; }
            .visualizer.active .bar:nth-child(2) { animation-delay: 0.2s; }
            .visualizer.active .bar:nth-child(3) { animation-delay: 0.4s; }
            .visualizer.active .bar:nth-child(4) { animation-delay: 0.1s; }
            .visualizer.active .bar:nth-child(5) { animation-delay: 0.3s; }

            @keyframes wave {
                0%, 100% { height: 8px; }
                50% { height: 28px; }
            }

            /* Controls */
            .controls {
                width: 100%;
                display: flex;
                gap: 12px;
                justify-content: center;
            }

            .btn {
                padding: 12px 20px;
                border-radius: 14px;
                font-weight: 600;
                font-size: 0.9375rem;
                border: none;
                cursor: pointer;
                display: flex;
                align-items: center;
                gap: 8px;
                transition: all 0.2s;
            }

            .btn-start {
                width: 100%;
                justify-content: center;
                background: var(--primary-grad);
                color: white;
                box-shadow: 0 4px 16px rgba(59, 130, 246, 0.4);
            }

            .btn-start:hover {
                transform: translateY(-1px);
                box-shadow: 0 6px 20px rgba(59, 130, 246, 0.55);
            }

            .btn-end {
                flex: 1;
                justify-content: center;
                background: var(--danger);
                color: white;
            }

            .btn-end:hover {
                background: #dc2626;
            }

            .btn-mute {
                width: 48px;
                height: 48px;
                border-radius: 14px;
                padding: 0;
                justify-content: center;
                background: var(--card-bg);
                color: var(--text);
                border: 1px solid var(--border);
            }

            .btn-mute:hover {
                background: rgba(255, 255, 255, 0.08);
            }

            .btn-mute.muted {
                background: rgba(239, 68, 68, 0.2);
                color: var(--danger);
                border-color: rgba(239, 68, 68, 0.4);
            }

            .call-timer {
                font-size: 0.8125rem;
                color: var(--text-muted);
                margin-top: 12px;
            }
        `;

        const template = `
            <style>${styles}</style>
            
            <div class="card" id="widget-card">
                <div class="header">
                    <span class="brand-title">AI Voice Agent</span>
                    <button class="close-btn" id="close-card-btn" title="Close">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <line x1="18" y1="6" x2="6" y2="18"></line>
                            <line x1="6" y1="6" x2="18" y2="18"></line>
                        </svg>
                    </button>
                </div>

                <div class="avatar-container">
                    <div class="status-ring" id="status-ring"></div>
                    <div class="avatar" id="avatar-box">
                        🤖
                    </div>
                </div>

                <div class="agent-name">${this.agentName}</div>
                <div class="status-badge" id="status-badge">
                    <span class="dot online" id="status-dot"></span>
                    <span id="status-text">Ready to talk</span>
                </div>

                <div class="visualizer" id="visualizer">
                    <div class="bar"></div>
                    <div class="bar"></div>
                    <div class="bar"></div>
                    <div class="bar"></div>
                    <div class="bar"></div>
                </div>

                <div class="controls" id="controls-box">
                    <button class="btn btn-start" id="start-btn">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"></path>
                            <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
                            <line x1="12" y1="19" x2="12" y2="22"></line>
                        </svg>
                        Start Conversation
                    </button>
                </div>

                <div class="call-timer" id="call-timer" style="display: none;">00:00</div>
            </div>

            <div class="launcher-btn" id="launcher-btn" title="Talk to AI Voice Agent">
                <div class="pulse-ring"></div>
                <svg viewBox="0 0 24 24">
                    <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/>
                    <path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/>
                </svg>
            </div>
        `;

        this.shadowRoot.innerHTML = template;
    }

    setupEventListeners() {
        const shadow = this.shadowRoot;
        const launcherBtn = shadow.getElementById('launcher-btn');
        const closeBtn = shadow.getElementById('close-card-btn');
        const card = shadow.getElementById('widget-card');
        const startBtn = shadow.getElementById('start-btn');

        launcherBtn.addEventListener('click', () => {
            card.classList.toggle('open');
        });

        closeBtn.addEventListener('click', () => {
            card.classList.remove('open');
        });

        startBtn.addEventListener('click', () => {
            if (this.state === 'idle' || this.state === 'ended' || this.state === 'error') {
                this.startCall();
            }
        });
    }

    async startCall() {
        try {
            this.updateState('connecting', 'Connecting...');
            const shadow = this.shadowRoot;
            const startBtn = shadow.getElementById('start-btn');
            if (startBtn) {
                startBtn.disabled = true;
                startBtn.innerHTML = `
                    <svg class="spin" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="12" cy="12" r="10" stroke-dasharray="32" stroke-dashoffset="10"></circle>
                    </svg>
                    Connecting...
                `;
            }

            // Initialize Web Audio Context at native browser rate
            // (We let the server handle any conversion to OpenAI's required 24kHz)
            const AudioCtx = window.AudioContext || window.webkitAudioContext;
            this.audioCtx = new AudioCtx();
            this.capturedSampleRate = this.audioCtx.sampleRate; // Capture for metadata
            if (this.audioCtx.state === 'suspended') {
                await this.audioCtx.resume();
            }

            // Get User Microphone
            this.micStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true
                }
            });

            // Prepare WebSocket URL with clean host formatting
            this.agentId = this.getAttribute('agent-id') || this.agentId || 'default';
            const cleanUrl = (this.getAttribute('server-url') || this.serverUrl || window.location.origin).replace(/\/+$/, '');
            const wsProtocol = cleanUrl.startsWith('https') ? 'wss:' : 'ws:';
            const host = cleanUrl.replace(/^https?:\/\//, '').replace(/\/+$/, '');
            const wsUrl = `${wsProtocol}//${host}/api/v1/stream/browser?agent_id=${encodeURIComponent(this.agentId)}`;
            console.log('VoiceAgentWidget: Connecting WebSocket to', wsUrl);

            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                console.log('VoiceAgentWidget: Connected to server stream');
                this.updateState('listening', 'Listening...');
                this.renderActiveCallControls();
                this.startTimer();
                this.setupMicrophoneProcessor();
            };

            this.ws.onmessage = (event) => {
                this.handleServerMessage(event.data);
            };

            this.ws.onerror = (err) => {
                console.error('VoiceAgentWidget: WebSocket error', err);
                this.updateState('error', 'Connection failed');
                this.endCall();
            };

            this.ws.onclose = () => {
                console.log('VoiceAgentWidget: WebSocket closed');
                if (this.state !== 'idle') {
                    this.updateState('ended', 'Call ended');
                    this.endCall();
                }
            };

        } catch (err) {
            console.error('VoiceAgentWidget: Error starting call', err);
            alert('Microphone permission is required to talk with the AI agent.');
            this.updateState('error', 'Microphone error');
        }
    }

    setupMicrophoneProcessor() {
        if (!this.micStream || !this.audioCtx) return;

        this.source = this.audioCtx.createMediaStreamSource(this.micStream);
        
        // Use ScriptProcessorNode for audio chunk extraction (resampled to Int16 PCM)
        const bufferSize = 4096;
        this.processor = this.audioCtx.createScriptProcessor(bufferSize, 1, 1);

        this.processor.onaudioprocess = (e) => {
            if (this.isMuted || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;

            const inputData = e.inputBuffer.getChannelData(0);
            
            // Convert Float32Array to Int16Array PCM
            const pcm16 = new Int16Array(inputData.length);
            for (let i = 0; i < inputData.length; i++) {
                const s = Math.max(-1, Math.min(1, inputData[i]));
                pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }

            // Convert to Base64 and send payload — include sample_rate so server can convert correctly
            const base64Audio = this.arrayBufferToBase64(pcm16.buffer);
            this.ws.send(JSON.stringify({
                event: 'media',
                media: {
                    payload: base64Audio,
                    sample_rate: this.capturedSampleRate || this.audioCtx.sampleRate
                }
            }));
        };

        this.source.connect(this.processor);
        this.processor.connect(this.audioCtx.destination);
    }

    handleServerMessage(dataStr) {
        try {
            const data = JSON.parse(dataStr);

            if (data.error || data.event === 'error') {
                const errText = data.error || 'Server connection error';
                console.error('VoiceAgentWidget: Server reported error:', errText);
                this.updateState('error', errText);
            } else if (data.event === 'audio' && data.audio) {
                // Incoming audio response from AI agent
                this.updateState('speaking', 'Speaking...');
                const sampleRate = data.sample_rate || 24000; // default to 24kHz (OpenAI Realtime native)
                this.playAudioChunk(data.audio, sampleRate);
            } else if (data.event === 'status') {
                if (data.status === 'listening') {
                    this.updateState('listening', 'Listening...');
                } else if (data.status === 'thinking') {
                    this.updateState('thinking', 'Thinking...');
                }
            } else if (data.event === 'clear') {
                // User interrupted agent - clear playback queue
                this.clearAudioQueue();
                this.updateState('listening', 'Listening...');
            }
        } catch (e) {
            console.error('Error parsing server message', e);
        }
    }

    async playAudioChunk(base64Audio, sampleRate = 24000) {
        if (!this.audioCtx) return;

        try {
            const arrayBuffer = this.base64ToArrayBuffer(base64Audio);
            
            // Decode raw PCM16 little-endian bytes to Float32
            const int16Array = new Int16Array(arrayBuffer);
            const float32Array = new Float32Array(int16Array.length);
            for (let i = 0; i < int16Array.length; i++) {
                float32Array[i] = int16Array[i] / (int16Array[i] < 0 ? 0x8000 : 0x7FFF);
            }

            // Use server-reported sample rate so audio plays at correct pitch/speed
            const audioBuffer = this.audioCtx.createBuffer(1, float32Array.length, sampleRate);
            audioBuffer.getChannelData(0).set(float32Array);

            const sourceNode = this.audioCtx.createBufferSource();
            sourceNode.buffer = audioBuffer;
            sourceNode.connect(this.audioCtx.destination);

            const currentTime = this.audioCtx.currentTime;
            if (this.nextStartTime < currentTime) {
                this.nextStartTime = currentTime;
            }

            sourceNode.start(this.nextStartTime);
            this.nextStartTime += audioBuffer.duration;

            sourceNode.onended = () => {
                if (this.audioCtx && this.audioCtx.currentTime >= this.nextStartTime - 0.05) {
                    if (this.state === 'speaking') {
                        this.updateState('listening', 'Listening...');
                    }
                }
            };
        } catch (e) {
            console.error('Error playing audio chunk', e);
        }
    }

    clearAudioQueue() {
        this.nextStartTime = this.audioCtx ? this.audioCtx.currentTime : 0;
    }

    renderActiveCallControls() {
        const shadow = this.shadowRoot;
        const controlsBox = shadow.getElementById('controls-box');
        
        controlsBox.innerHTML = `
            <button class="btn btn-mute" id="mute-btn" title="Mute Microphone">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
                    <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
                    <line x1="12" y1="19" x2="12" y2="23"></line>
                    <line x1="8" y1="23" x2="16" y2="23"></line>
                </svg>
            </button>
            <button class="btn btn-end" id="end-btn">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M10.68 13.31a16 16 0 0 0 3.41 2.6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7 2 2 0 0 1 1.72 2v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.42 19.42 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 2.59 3.4z" transform="rotate(135 12 12)"></path>
                </svg>
                End Call
            </button>
        `;

        shadow.getElementById('end-btn').addEventListener('click', () => {
            this.endCall();
        });

        shadow.getElementById('mute-btn').addEventListener('click', () => {
            this.isMuted = !this.isMuted;
            const muteBtn = shadow.getElementById('mute-btn');
            if (this.isMuted) {
                muteBtn.classList.add('muted');
                this.updateState('muted', 'Microphone muted');
            } else {
                muteBtn.classList.remove('muted');
                this.updateState('listening', 'Listening...');
            }
        });
    }

    endCall() {
        this.stopTimer();

        if (this.processor) {
            this.processor.disconnect();
            this.processor = null;
        }
        if (this.source) {
            this.source.disconnect();
            this.source = null;
        }
        if (this.micStream) {
            this.micStream.getTracks().forEach(track => track.stop());
            this.micStream = null;
        }
        if (this.audioCtx) {
            this.audioCtx.close();
            this.audioCtx = null;
        }
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }

        this.updateState('idle', 'Ready to talk');
        
        const shadow = this.shadowRoot;
        const controlsBox = shadow.getElementById('controls-box');
        controlsBox.innerHTML = `
            <button class="btn btn-start" id="start-btn">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"></path>
                    <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
                    <line x1="12" y1="19" x2="12" y2="22"></line>
                </svg>
                Start Conversation
            </button>
        `;

        shadow.getElementById('start-btn').addEventListener('click', () => {
            this.startCall();
        });
    }

    updateState(newState, label) {
        this.state = newState;
        const shadow = this.shadowRoot;

        const statusText = shadow.getElementById('status-text');
        const statusDot = shadow.getElementById('status-dot');
        const statusRing = shadow.getElementById('status-ring');
        const visualizer = shadow.getElementById('visualizer');

        if (statusText) statusText.textContent = label;

        if (statusRing) {
            statusRing.className = 'status-ring ' + (newState === 'listening' ? 'listening' : newState === 'speaking' ? 'speaking' : newState === 'thinking' ? 'thinking' : '');
        }

        if (visualizer) {
            if (newState === 'listening' || newState === 'speaking') {
                visualizer.classList.add('active');
            } else {
                visualizer.classList.remove('active');
            }
        }

        if (statusDot) {
            if (newState === 'idle') {
                statusDot.className = 'dot online';
            } else if (newState === 'error') {
                statusDot.className = 'dot error';
            } else {
                statusDot.className = 'dot active';
            }
        }
    }

    startTimer() {
        this.callStartTime = Date.now();
        const timerEl = this.shadowRoot.getElementById('call-timer');
        if (timerEl) timerEl.style.display = 'block';

        this.timerInterval = setInterval(() => {
            const seconds = Math.floor((Date.now() - this.callStartTime) / 1000);
            const m = String(Math.floor(seconds / 60)).padStart(2, '0');
            const s = String(seconds % 60).padStart(2, '0');
            if (timerEl) timerEl.textContent = `${m}:${s}`;
        }, 1000);
    }

    stopTimer() {
        if (this.timerInterval) {
            clearInterval(this.timerInterval);
            this.timerInterval = null;
        }
        const timerEl = this.shadowRoot.getElementById('call-timer');
        if (timerEl) timerEl.style.display = 'none';
    }

    arrayBufferToBase64(buffer) {
        let binary = '';
        const bytes = new Uint8Array(buffer);
        const len = bytes.byteLength;
        for (let i = 0; i < len; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return window.btoa(binary);
    }

    base64ToArrayBuffer(base64) {
        const binaryString = window.atob(base64);
        const len = binaryString.length;
        const bytes = new Uint8Array(len);
        for (let i = 0; i < len; i++) {
            bytes[i] = binaryString.charCodeAt(i);
        }
        return bytes.buffer;
    }
}

// Register Custom Element
if (!customElements.get('agent-stream-voice')) {
    customElements.define('agent-stream-voice', VoiceAgentWidget);
}

// Auto-inject widget element if not already present on page
(function autoInjectWidget() {
    function init() {
        if (document.querySelector('agent-stream-voice')) return;
        const scripts = document.querySelectorAll('script');
        let agentId = null;
        let serverUrl = null;
        for (let s of scripts) {
            if (s.src && s.src.includes('voice-agent-widget.js')) {
                agentId = s.getAttribute('data-agent-id') || s.getAttribute('agent-id');
                serverUrl = s.getAttribute('data-server-url') || s.getAttribute('server-url');
                if (agentId) break;
            }
        }
        if (agentId) {
            const el = document.createElement('agent-stream-voice');
            el.setAttribute('agent-id', agentId);
            if (serverUrl) el.setAttribute('server-url', serverUrl);
            document.body.appendChild(el);
            console.log('VoiceAgentWidget: Auto-injected widget for agent_id:', agentId);
        }
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
