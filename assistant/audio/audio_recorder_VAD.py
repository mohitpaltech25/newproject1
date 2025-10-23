import threading
import subprocess
import os
import queue
import time
import uuid
from utils.logger_config import logger
import numpy as np
import wave
import webrtcvad

project_dir = os.path.dirname(os.path.abspath(__file__))
output_dir = os.path.join(project_dir, "..", "..", "audio_segments")
output_dir = os.path.abspath(output_dir)


class Frame:
    """Represents a "frame" of audio data."""

    def __init__(self, bytes_data: bytes, timestamp: float, duration: float):
        self.bytes = bytes_data
        self.timestamp = timestamp
        self.duration = duration


class VADProcessor:
    """Voice Activity Detection processor using WebRTC VAD."""

    def __init__(self, aggressiveness: int = 2, sample_rate: int = 16000, frame_duration_ms: int = 30):
        self.vad = webrtcvad.Vad(aggressiveness)
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_length = int(sample_rate * frame_duration_ms / 1000)
        self.bytes_per_frame = self.frame_length * 2  # 16-bit audio = 2 bytes per sample

    def frame_generator(self, audio_data: bytes | np.ndarray, sample_rate: int):
        frame_duration = self.frame_duration_ms / 1000.0
        frames: list[Frame] = []

        if isinstance(audio_data, bytes):
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
        else:
            audio_array = audio_data

        samples_per_frame = int(sample_rate * frame_duration)
        for i in range(0, len(audio_array), samples_per_frame):
            frame_data = audio_array[i : i + samples_per_frame]
            if len(frame_data) == samples_per_frame:
                frame_bytes = frame_data.astype(np.int16).tobytes()
                timestamp = i / sample_rate
                frames.append(Frame(frame_bytes, timestamp, frame_duration))

        return frames

    def is_speech(self, frame_bytes: bytes, sample_rate: int) -> bool:
        try:
            expected_frame_size = int(sample_rate * self.frame_duration_ms / 1000) * 2

            if len(frame_bytes) != expected_frame_size:
                if len(frame_bytes) < expected_frame_size:
                    frame_bytes = frame_bytes + b"\x00" * (expected_frame_size - len(frame_bytes))
                else:
                    frame_bytes = frame_bytes[:expected_frame_size]

            audio_data = np.frombuffer(frame_bytes, dtype=np.int16)

            energy = np.sum(audio_data.astype(np.float32) ** 2)
            energy_threshold = 1_000_000
            if energy < energy_threshold:
                return False

            zero_crossings = np.sum(np.diff(np.sign(audio_data)) != 0)
            zcr = zero_crossings / len(audio_data)
            if zcr < 0.005 or zcr > 0.3:
                return False

            webrtc_result = self.vad.is_speech(frame_bytes, sample_rate)
            return webrtc_result and energy >= energy_threshold and 0.005 <= zcr <= 0.3
        except Exception as e:
            logger.warning(f"VAD error: {e}")
            return False


class HybridChunker:
    """Hybrid chunking strategy combining VAD with time-based limits."""

    def __init__(
        self,
        min_chunk_duration: float = 1.0,
        max_chunk_duration: float = 15.0,
        silence_timeout: float = 2.0,
        speech_pad_ms: int = 300,
    ):
        self.min_chunk_duration = min_chunk_duration
        self.max_chunk_duration = max_chunk_duration
        self.silence_timeout = silence_timeout
        self.speech_pad_ms = speech_pad_ms

        self.current_chunk_frames: list[tuple[Frame, bool]] = []
        self.chunk_start_time: float | None = None
        self.last_speech_time: float | None = None
        self.in_speech_segment: bool = False

    def should_end_chunk(self, current_time: float) -> bool:
        if not self.chunk_start_time:
            return False

        chunk_duration = current_time - self.chunk_start_time
        if chunk_duration >= self.max_chunk_duration:
            return True

        if (
            chunk_duration >= self.min_chunk_duration
            and self.last_speech_time
            and (current_time - self.last_speech_time) >= self.silence_timeout
        ):
            return True

        return False

    def add_frame(self, frame: Frame, is_speech: bool):
        current_time = time.time()
        if not self.chunk_start_time:
            self.chunk_start_time = current_time

        if is_speech:
            self.last_speech_time = current_time
            self.in_speech_segment = True

        self.current_chunk_frames.append((frame, is_speech))
        if self.should_end_chunk(current_time):
            return self._finalize_chunk()
        return None

    def _finalize_chunk(self):
        if not self.current_chunk_frames:
            return None

        chunk_data = {
            "frames": self.current_chunk_frames.copy(),
            "start_time": self.chunk_start_time,
            "end_time": time.time(),
            "frame_count": len(self.current_chunk_frames),
        }

        self.current_chunk_frames = []
        self.chunk_start_time = None
        self.last_speech_time = None
        self.in_speech_segment = False

        return chunk_data

    def flush(self):
        if self.current_chunk_frames:
            return self._finalize_chunk()
        return None


def drain_stderr(pipe):
    try:
        with pipe:
            for _ in iter(pipe.readline, b""):
                pass
    except Exception as e:
        logger.error(f"Error reading ffmpeg stderr: {e}")


class AudioRecorderThread(threading.Thread):
    def __init__(
        self,
        segment_duration: int = 10,
        output_dir: str = output_dir,
        device_name: str = "Virtual-Sink.monitor",
        queue_size: int = 50,
        use_vad: bool = True,
        use_hybrid_chunking: bool = True,
        vad_aggressiveness: int = 2,
        min_chunk_duration: float = 1.0,
        max_chunk_duration: float = 15.0,
        silence_timeout: float = 2.0,
    ):
        super().__init__(name="AudioRecorder")
        self.segment_duration = segment_duration
        self.output_dir = output_dir
        self.device_name = device_name
        self.audio_queue: queue.Queue[str] = queue.Queue(maxsize=queue_size)
        self.running = threading.Event()
        self.running.set()

        self.use_vad = use_vad
        self.use_hybrid_chunking = use_hybrid_chunking and use_vad

        if self.use_vad:
            self.vad_processor = VADProcessor(aggressiveness=vad_aggressiveness)
            if self.use_hybrid_chunking:
                self.chunker = HybridChunker(
                    min_chunk_duration=min_chunk_duration,
                    max_chunk_duration=max_chunk_duration,
                    silence_timeout=silence_timeout,
                )

        os.makedirs(self.output_dir, exist_ok=True)

    def _save_audio_chunk(self, audio_data: bytes, timestamp: int | None = None):
        if timestamp is None:
            timestamp = int(time.time() * 1000)
        filename = f"segment_{uuid.uuid4().hex}_{timestamp}.wav"
        filepath = os.path.join(self.output_dir, filename)

        try:
            with wave.open(filepath, "wb") as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(16000)  # 16kHz
                wav_file.writeframes(audio_data)

            try:
                self.audio_queue.put_nowait(filepath)
                logger.info(f"[Recorder] Queued audio: {filepath}")
            except queue.Full:
                logger.warning(f"[Recorder] Audio queue full. Dropping: {filepath}")
                os.remove(filepath)
        except Exception as e:
            logger.error(f"[Recorder] Error saving audio chunk: {e}")

    def _run_with_vad(self):
        logger.info("[Recorder] Started VAD-enabled recording")
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "pulse",
            "-i",
            self.device_name,
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "s16le",
            "-loglevel",
            "error",
            "pipe:1",
        ]

        process = None
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            threading.Thread(target=drain_stderr, args=(process.stderr,), daemon=True).start()

            chunk_buffer = bytearray()
            frame_duration_ms = 30
            frame_size = int(16000 * frame_duration_ms / 1000) * 2

            while self.running.is_set():
                audio_chunk = process.stdout.read(frame_size)
                if not audio_chunk:
                    break

                chunk_buffer.extend(audio_chunk)
                while len(chunk_buffer) >= frame_size:
                    frame_data = bytes(chunk_buffer[:frame_size])
                    chunk_buffer = chunk_buffer[frame_size:]

                    frame = Frame(frame_data, time.time(), frame_duration_ms / 1000.0)
                    is_speech = self.vad_processor.is_speech(frame_data, 16000)

                    if self.use_hybrid_chunking:
                        completed_chunk = self.chunker.add_frame(frame, is_speech)
                        if completed_chunk:
                            combined_audio = bytearray()
                            for chunk_frame, _ in completed_chunk["frames"]:
                                combined_audio.extend(chunk_frame.bytes)
                            timestamp = int(completed_chunk["start_time"] * 1000)
                            self._save_audio_chunk(bytes(combined_audio), timestamp)
                    else:
                        if not hasattr(self, "_speech_buffer"):
                            self._speech_buffer = bytearray()
                            self._last_speech_time = time.time()

                        if is_speech:
                            self._speech_buffer.extend(frame_data)
                            self._last_speech_time = time.time()
                        else:
                            if self._speech_buffer and time.time() - self._last_speech_time > 1.0:
                                if len(self._speech_buffer) > 16000:  # 1s
                                    self._save_audio_chunk(bytes(self._speech_buffer))
                                self._speech_buffer = bytearray()
        except Exception as e:
            logger.error(f"[Recorder] VAD recording error: {e}")
        finally:
            if process:
                process.terminate()
                process.wait()

            if self.use_hybrid_chunking and hasattr(self, "chunker"):
                remaining_chunk = self.chunker.flush()
                if remaining_chunk:
                    combined_audio = bytearray()
                    for frame, _ in remaining_chunk["frames"]:
                        combined_audio.extend(frame.bytes)
                    self._save_audio_chunk(bytes(combined_audio))

    def _run_without_vad(self):
        logger.info("[Recorder] Started traditional time-based recording")
        while self.running.is_set():
            try:
                start_time = time.time()
                timestamp = int(start_time * 1000)
                filename = f"segment_{uuid.uuid4().hex}_{timestamp}.wav"
                filepath = os.path.join(self.output_dir, filename)

                cmd = [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "pulse",
                    "-i",
                    self.device_name,
                    "-t",
                    str(self.segment_duration),
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-loglevel",
                    "error",
                    filepath,
                ]

                process = subprocess.Popen(cmd)
                process.wait()

                logger.info(f"[Recorder] Captured audio: {filepath} ({time.time() - start_time:.2f}s)")

                try:
                    self.audio_queue.put_nowait(filepath)
                    logger.info(f"[Recorder] Queued audio: {filepath}")
                except queue.Full:
                    logger.warning(f"[Recorder] Audio queue full. Dropping: {filepath}")
                    os.remove(filepath)

            except subprocess.CalledProcessError as e:
                logger.error(f"[Recorder] FFmpeg error: {e}")
            except Exception as e:
                logger.error(f"[Recorder] Unexpected error: {e}")

    def run(self):
        logger.info(f"[Recorder] Started recording thread (VAD: {self.use_vad}, Hybrid: {self.use_hybrid_chunking})")
        if self.use_vad:
            self._run_with_vad()
        else:
            self._run_without_vad()

    def stop(self):
        logger.info("[Recorder] Stopping...")
        self.running.clear()

    def get_audio_segment(self, timeout: float = 1.0):
        try:
            return self.audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None
