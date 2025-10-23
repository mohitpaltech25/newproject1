#assistant/audio/auido_recorder_VAD.py
import threading
import subprocess
import os
import queue
import time
import uuid
from utils.logger_config import logger
import datetime
import signal
import numpy as np
import wave
import collections
import webrtcvad

project_dir = os.path.dirname(os.path.abspath(__file__))
output_dir = os.path.join(project_dir, "audio_segments")

class Frame:
    """Represents a "frame" of audio data."""
    def __init__(self, bytes, timestamp, duration):
        self.bytes = bytes
        self.timestamp = timestamp
        self.duration = duration

class VADProcessor:
    """Voice Activity Detection processor using WebRTC VAD."""
    
    def __init__(self, aggressiveness=2, sample_rate=16000, frame_duration_ms=30):
        """
        Initialize VAD processor.
        
        Args:
            aggressiveness: VAD aggressiveness (0-3, higher = more aggressive)
            sample_rate: Audio sample rate (8000, 16000, 32000, or 48000)
            frame_duration_ms: Frame duration in milliseconds (10, 20, or 30)
        """
        self.vad = webrtcvad.Vad(aggressiveness)
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_length = int(sample_rate * frame_duration_ms / 1000)
        self.bytes_per_frame = self.frame_length * 2  # 16-bit audio = 2 bytes per sample

    def frame_generator(self, audio_data, sample_rate):
        """Generate audio frames from raw audio data."""
        frame_duration = self.frame_duration_ms / 1000.0
        frames = []
        
        # Convert to numpy array if needed
        if isinstance(audio_data, bytes):
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
        else:
            audio_array = audio_data
            
        # Generate frames
        samples_per_frame = int(sample_rate * frame_duration)
        for i in range(0, len(audio_array), samples_per_frame):
            frame_data = audio_array[i:i + samples_per_frame]
            if len(frame_data) == samples_per_frame:
                frame_bytes = frame_data.astype(np.int16).tobytes()
                timestamp = i / sample_rate
                frames.append(Frame(frame_bytes, timestamp, frame_duration))
                
        return frames

    def is_speech(self, frame_bytes, sample_rate):
        """
        Check if frame contains speech with additional processing.
        
        Args:
            frame_bytes: Raw audio frame bytes (16-bit PCM)
            sample_rate: Sample rate of the audio
            
        Returns:
            bool: True if speech is detected, False otherwise
        """
        try:
            # Ensure we have the correct frame size for WebRTC VAD
            expected_frame_size = int(sample_rate * self.frame_duration_ms / 1000) * 2
            
            if len(frame_bytes) != expected_frame_size:
                # Pad or truncate to expected size
                if len(frame_bytes) < expected_frame_size:
                    # Pad with zeros
                    frame_bytes = frame_bytes + b'\x00' * (expected_frame_size - len(frame_bytes))
                else:
                    # Truncate to expected size
                    frame_bytes = frame_bytes[:expected_frame_size]
            
            # Convert to numpy array for additional processing
            audio_data = np.frombuffer(frame_bytes, dtype=np.int16)
            
            # Additional speech detection heuristics
            # 1. Check for sufficient energy (not just silence)
            energy = np.sum(audio_data.astype(np.float32) ** 2)
            energy_threshold = 1000000  # Adjust based on your audio levels
            
            if energy < energy_threshold:
                return False  # Too quiet to be speech
            
            # 2. Check for zero crossing rate (speech has moderate ZCR)
            zero_crossings = np.sum(np.diff(np.sign(audio_data)) != 0)
            zcr = zero_crossings / len(audio_data)
            
            # Speech typically has ZCR between 0.01 and 0.25
            if zcr < 0.005 or zcr > 0.3:
                return False  # Likely not speech
            
            # 3. Use WebRTC VAD as primary detector
            webrtc_result = self.vad.is_speech(frame_bytes, sample_rate)
            
            # 4. Combine heuristics with WebRTC result
            # Only return True if WebRTC detects speech AND our heuristics pass
            return webrtc_result and energy >= energy_threshold and 0.005 <= zcr <= 0.3
            
        except Exception as e:
            logger.warning(f"VAD error: {e}")
            return False

class HybridChunker:
    """Hybrid chunking strategy combining VAD with time-based limits."""
    
    def __init__(self, 
                 min_chunk_duration=1.0,
                 max_chunk_duration=30.0,
                 silence_timeout=2.0,
                 speech_pad_ms=300):
        """
        Initialize hybrid chunker.
        
        Args:
            min_chunk_duration: Minimum chunk duration in seconds
            max_chunk_duration: Maximum chunk duration in seconds
            silence_timeout: Silence duration before ending chunk (seconds)
            speech_pad_ms: Padding around speech segments (milliseconds)
        """
        self.min_chunk_duration = min_chunk_duration
        self.max_chunk_duration = max_chunk_duration
        self.silence_timeout = silence_timeout
        self.speech_pad_ms = speech_pad_ms
        
        # State tracking
        self.current_chunk_frames = []
        self.chunk_start_time = None
        self.last_speech_time = None
        self.in_speech_segment = False

    def should_end_chunk(self, current_time):
        """Determine if current chunk should be ended."""
        if not self.chunk_start_time:
            return False
            
        chunk_duration = current_time - self.chunk_start_time
        
        # Force end if max duration reached
        if chunk_duration >= self.max_chunk_duration:
            return True
            
        # End if we have enough duration and sufficient silence
        if (chunk_duration >= self.min_chunk_duration and 
            self.last_speech_time and 
            (current_time - self.last_speech_time) >= self.silence_timeout):
            return True
            
        return False

    def add_frame(self, frame, is_speech):
        """Add frame to current chunk and return completed chunk if ready."""
        current_time = time.time()
        
        # Initialize chunk if needed
        if not self.chunk_start_time:
            self.chunk_start_time = current_time
            
        # Update speech tracking
        if is_speech:
            self.last_speech_time = current_time
            self.in_speech_segment = True
            
        # Add frame to current chunk
        self.current_chunk_frames.append((frame, is_speech))
        
        # Check if chunk should end
        if self.should_end_chunk(current_time):
            return self._finalize_chunk()
            
        return None

    def _finalize_chunk(self):
        """Finalize and return current chunk."""
        if not self.current_chunk_frames:
            return None
            
        # Create chunk data
        chunk_data = {
            'frames': self.current_chunk_frames.copy(),
            'start_time': self.chunk_start_time,
            'end_time': time.time(),
            'frame_count': len(self.current_chunk_frames)
        }
        
        # Reset state
        self.current_chunk_frames = []
        self.chunk_start_time = None
        self.last_speech_time = None
        self.in_speech_segment = False
        
        return chunk_data

    def flush(self):
        """Flush any remaining frames as a chunk."""
        if self.current_chunk_frames:
            return self._finalize_chunk()
        return None

def drain_stderr(pipe):
    try:
        with pipe:
            for line in iter(pipe.readline, b''):
                line_str = line.decode(errors='ignore').strip()
                if line_str:
                    # logger.error(f"[ffmpeg stderr] {line_str}")
                    pass
    except Exception as e:
        logger.error(f"Error reading ffmpeg stderr: {e}")
class AudioRecorderThread(threading.Thread):
    def __init__(self, 
                 segment_duration=10, 
                 output_dir=output_dir, 
                 device_name="Virtual-Sink.monitor",
                 queue_size=50,
                 use_vad=True,
                 use_hybrid_chunking=True,
                 vad_aggressiveness=2,
                 min_chunk_duration=1.0,
                 max_chunk_duration=15.0,
                 silence_timeout=2.0):
        """
        Initialize audio recorder with VAD and hybrid chunking options.
        
        Args:
            segment_duration: Fallback segment duration (used when VAD disabled)
            output_dir: Directory to save audio segments
            device_name: Audio device name
            queue_size: Queue size for audio segments
            use_vad: Enable Voice Activity Detection
            use_hybrid_chunking: Enable hybrid chunking (requires use_vad=True)
            vad_aggressiveness: VAD aggressiveness level (0-3)
            min_chunk_duration: Minimum chunk duration in seconds
            max_chunk_duration: Maximum chunk duration in seconds
            silence_timeout: Silence timeout in seconds
        """
        super().__init__()
        self.segment_duration = segment_duration
        self.output_dir = output_dir
        self.device_name = device_name
        self.audio_queue = queue.Queue(maxsize=queue_size)
        self.running = threading.Event()
        self.running.set()
        
        # VAD and chunking configuration
        self.use_vad = use_vad
        self.use_hybrid_chunking = use_hybrid_chunking and use_vad
        
        # Initialize VAD processor if enabled
        if self.use_vad:
            self.vad_processor = VADProcessor(aggressiveness=vad_aggressiveness)
            
            # Initialize hybrid chunker if enabled
            if self.use_hybrid_chunking:
                self.chunker = HybridChunker(
                    min_chunk_duration=min_chunk_duration,
                    max_chunk_duration=max_chunk_duration,
                    silence_timeout=silence_timeout
                )
        
        os.makedirs(self.output_dir, exist_ok=True)

    def _save_audio_chunk(self, audio_data, timestamp=None):
        """Save audio data to file and queue it."""
        if timestamp is None:
            timestamp = int(time.time() * 1000)
            
        filename = f"segment_{uuid.uuid4().hex}_{timestamp}.wav"
        filepath = os.path.join(self.output_dir, filename)
        
        try:
            # Save audio data as WAV file
            with wave.open(filepath, 'wb') as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(16000)  # 16kHz
                wav_file.writeframes(audio_data)
            
            # Queue the file
            try:
                self.audio_queue.put_nowait(filepath)
                logger.info(f"[Recorder] Queued audio: {filepath}")
            except queue.Full:
                logger.warning(f"[Recorder] Audio queue full. Dropping: {filepath}")
                os.remove(filepath)
                
        except Exception as e:
            logger.error(f"[Recorder] Error saving audio chunk: {e}")

    def _run_with_vad(self):
        """Run recording with VAD and optional hybrid chunking."""
        logger.info("[Recorder] Started VAD-enabled recording")
        
        # Start continuous audio capture
        cmd = [
            "ffmpeg", "-y",
            "-f", "pulse",
            "-i", self.device_name,
            "-ac", "1",
            "-ar", "16000",
            "-f", "s16le",  # Raw 16-bit audio
            "-loglevel", "error",
            "pipe:1"
        ]
        
        process = None
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Drain stderr asynchronously
            threading.Thread(target=drain_stderr, args=(process.stderr,), daemon=True).start()
            
            chunk_buffer = bytearray()
            frame_duration_ms = 30
            frame_size = int(16000 * frame_duration_ms / 1000) * 2  # 16-bit = 2 bytes per sample
            
            while self.running.is_set():
                # Read audio data
                audio_chunk = process.stdout.read(frame_size)
                if not audio_chunk:
                    break
                    
                chunk_buffer.extend(audio_chunk)
                
                # Process frames when we have enough data
                while len(chunk_buffer) >= frame_size:
                    frame_data = bytes(chunk_buffer[:frame_size])
                    chunk_buffer = chunk_buffer[frame_size:]
                    
                    # Create frame object
                    frame = Frame(frame_data, time.time(), frame_duration_ms / 1000.0)
                    
                    # Check for speech
                    is_speech = self.vad_processor.is_speech(frame_data, 16000)
                    
                    if self.use_hybrid_chunking:
                        # Use hybrid chunking
                        completed_chunk = self.chunker.add_frame(frame, is_speech)
                        if completed_chunk:
                            # Combine all frames into audio data
                            combined_audio = bytearray()
                            for chunk_frame, _ in completed_chunk['frames']:
                                combined_audio.extend(chunk_frame.bytes)
                            
                            timestamp = int(completed_chunk['start_time'] * 1000)
                            self._save_audio_chunk(bytes(combined_audio), timestamp)
                    else:
                        # Simple VAD: collect speech frames until silence
                        if not hasattr(self, '_speech_buffer'):
                            self._speech_buffer = bytearray()
                            self._last_speech_time = time.time()
                            
                        if is_speech:
                            self._speech_buffer.extend(frame_data)
                            self._last_speech_time = time.time()
                        else:
                            # Check if we should save accumulated speech
                            if (self._speech_buffer and 
                                time.time() - self._last_speech_time > 1.0):  # 1 second silence
                                
                                if len(self._speech_buffer) > 16000:  # At least 1 second of audio
                                    self._save_audio_chunk(bytes(self._speech_buffer))
                                
                                self._speech_buffer = bytearray()
                                
        except Exception as e:
            logger.error(f"[Recorder] VAD recording error: {e}")
        finally:
            if process:
                process.terminate()
                process.wait()
                
            # Flush any remaining data
            if self.use_hybrid_chunking and hasattr(self, 'chunker'):
                remaining_chunk = self.chunker.flush()
                if remaining_chunk:
                    combined_audio = bytearray()
                    for frame, _ in remaining_chunk['frames']:
                        combined_audio.extend(frame.bytes)
                    self._save_audio_chunk(bytes(combined_audio))

    def _run_without_vad(self):
        """Run recording with traditional time-based chunking."""
        logger.info("[Recorder] Started traditional time-based recording")
        
        while self.running.is_set():
            try:
                start_time = time.time()
                timestamp = int(start_time * 1000)
                filename = f"segment_{uuid.uuid4().hex}_{timestamp}.wav"
                filepath = os.path.join(self.output_dir, filename)

                cmd = [
                    "ffmpeg", "-y",
                    "-f", "pulse",
                    "-i", self.device_name,
                    "-t", str(self.segment_duration),
                    "-ac", "1",
                    "-ar", "16000",
                    "-loglevel", "error",
                    filepath
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
        """Main thread execution."""
        logger.info(f"[Recorder] Started recording thread (VAD: {self.use_vad}, Hybrid: {self.use_hybrid_chunking})")
        
        if self.use_vad:
            self._run_with_vad()
        else:
            self._run_without_vad()

    def stop(self):
        """Stop the recording thread."""
        logger.info("[Recorder] Stopping...")
        self.running.clear()

    def get_audio_segment(self, timeout=1.0):
        """Get the next available audio segment."""
        try:
            return self.audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None


# Example usage:
if __name__ == "__main__":
    # Example 1: Traditional time-based recording
    recorder_traditional = AudioRecorderThread(
        segment_duration=10,
        use_vad=False
    )
    
    # Example 2: VAD with simple speech detection
    recorder_vad = AudioRecorderThread(
        use_vad=True,
        use_hybrid_chunking=False,
        vad_aggressiveness=2
    )
    
    # Example 3: Full VAD with hybrid chunking
    recorder_hybrid = AudioRecorderThread(
        use_vad=True,
        use_hybrid_chunking=True,
        vad_aggressiveness=2,
        min_chunk_duration=2.0,
        max_chunk_duration=20.0,
        silence_timeout=1.5
    )
    
    # Start recording
    recorder_hybrid.start()
    #recorder_vad.start()
    #recorder_traditional.start()
    
    
    try:
        # Process audio segments
        while True:
            segment_path = recorder_hybrid.get_audio_segment(timeout=2.0)
            if segment_path:
                print(f"Got audio segment: {segment_path}")
                # Process the segment here
            else:
                print("No audio segment available")
    except KeyboardInterrupt:
        print("Stopping recorder...")
        recorder_hybrid.stop()
        recorder_hybrid.join()