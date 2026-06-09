"""
Output Handler Module (Windows version)

This module manages text output and clipboard operations including:
- Sequential text output queue management
- Keyboard and clipboard text insertion
- Thread-safe output coordination
- Transcript history management
- Send-after-insert functionality (press Enter to send messages)
- Auto-insert toggle (process only, insert later)

Windows Adaptations:
- Uses keyboard module for keyboard.write() and keyboard.send() (text output)
- Ctrl+V instead of Cmd+V for clipboard insertion
- Active modifier key polling with GetAsyncKeyState via is_key_pressed() (hook-free)

Classes:
    TranscriptionTask: Data class for transcription tasks
    OutputManager: Manages the output queue and text insertion
"""

import time
import queue
import logging
import threading
import keyboard
import pyperclip
from hotkey_manager import is_key_pressed
import pyautogui
from dataclasses import dataclass
from typing import Optional, Dict, Deque, List
from collections import deque

from config import (
    TRANSCRIPT_HISTORY_SIZE, OUTPUT_QUEUE_TIMEOUT,
    CLIPBOARD_RESTORE_DELAY, KEY_RELEASE_DELAY
)

logger = logging.getLogger('Thoughtborne.OutputHandler')


@dataclass
class TranscriptionTask:
    """Represents a transcription task in the output queue"""
    sequence_number: int
    timestamp: str
    transcript: Optional[str] = None
    is_complete: bool = False
    is_error: bool = False
    is_immediate: bool = False  # For "insert last text" functionality
    use_clipboard: bool = False  # Whether to use clipboard for insertion
    wait_for_key_release: bool = False  # Whether to wait for key release before inserting
    trigger_keys: Optional[List[str]] = None  # Which keys to wait for
    auto_insert: bool = True  # Whether to automatically insert text (False = only save for later)
    send_after_insert: bool = False  # Whether to press Enter after inserting (for sending messages)


class OutputManager:
    """Manages sequential text output and clipboard operations (Windows version)"""

    def __init__(self, on_task_complete_callback=None):
        """
        Initialize the output manager

        Args:
            on_task_complete_callback: Optional callback function called after task completion
        """
        self.output_queue: Dict[int, TranscriptionTask] = {}
        self.output_queue_lock = threading.Lock()
        self.output_condition = threading.Condition(self.output_queue_lock)
        self.on_task_complete = on_task_complete_callback

        self.next_sequence_to_output = 0
        self.sequence_counter = 0
        self.sequence_lock = threading.Lock()

        # Separate counter for immediate tasks (negative sequence numbers)
        self.immediate_counter = 0
        self.immediate_lock = threading.Lock()

        self.last_transcript = ""
        self.transcript_lock = threading.Lock()

        self.transcript_history: Deque[str] = deque(maxlen=TRANSCRIPT_HISTORY_SIZE)
        self.history_lock = threading.Lock()

        self.keyboard_lock = threading.Lock()
        self.running = True

        # Start output manager thread
        self.output_thread = threading.Thread(
            target=self._output_manager_thread,
            daemon=True,
            name="OutputManager"
        )
        self.output_thread.start()
        logger.info("Output manager initialized and started (Windows version)")

    def get_next_sequence_number(self) -> int:
        """Get the next sequence number (thread-safe)"""
        with self.sequence_lock:
            current = self.sequence_counter
            self.sequence_counter += 1
            return current

    def get_next_immediate_sequence_number(self) -> int:
        """Get the next immediate sequence number (negative, thread-safe)"""
        with self.immediate_lock:
            self.immediate_counter -= 1
            return self.immediate_counter

    def add_task(self, task: TranscriptionTask):
        """Add a task to the output queue"""
        with self.output_condition:
            self.output_queue[task.sequence_number] = task
            self.output_condition.notify()

    def update_last_transcript(self, text: str):
        """Update the last transcript (thread-safe)"""
        with self.transcript_lock:
            self.last_transcript = text

        with self.history_lock:
            self.transcript_history.append(text)

    def get_last_transcript(self) -> str:
        """Get the last transcript (thread-safe)"""
        with self.transcript_lock:
            return self.last_transcript

    def insert_last_transcript(self, use_clipboard: bool = False, wait_for_keys: List[str] = None, send_after_insert: bool = False):
        """Insert the last transcript via the queue"""
        transcript = self.get_last_transcript()

        if transcript:
            mode = "Clipboard" if use_clipboard else "Keyboard"
            send_info = " + Send" if send_after_insert else ""
            # Use negative sequence number for immediate tasks
            seq_num = self.get_next_immediate_sequence_number()
            logger.debug(f"Inserting last text via queue (Seq: {seq_num}, Length: {len(transcript)}, Mode: {mode}{send_info})")

            task = TranscriptionTask(
                sequence_number=seq_num,
                timestamp=time.strftime('%Y%m%d_%H%M%S'),
                transcript=transcript,
                is_complete=True,
                is_immediate=True,
                use_clipboard=use_clipboard,
                wait_for_key_release=(wait_for_keys is not None),
                trigger_keys=wait_for_keys,
                send_after_insert=send_after_insert
            )
            self.add_task(task)

    def _ensure_no_modifiers_pressed(self, max_wait=2.0):
        """
        Wait until no modifier keys are pressed (Windows version)

        Does NOT send any key events - only waits for the user to release naturally.
        Uses GetAsyncKeyState via is_key_pressed() to poll modifier state (hook-free).

        Args:
            max_wait: Maximum time to wait in seconds (default: 2.0)

        Returns:
            True if no modifiers pressed, False if timeout
        """
        start_time = time.time()

        while time.time() - start_time < max_wait:
            modifiers_pressed = False

            for key in ['ctrl', 'alt', 'shift', 'win']:
                if is_key_pressed(key):
                    modifiers_pressed = True
                    logger.debug(f"Modifier {key} still pressed, waiting...")
                    break

            if not modifiers_pressed:
                return True

            time.sleep(0.05)

        logger.warning("Timeout waiting for modifier keys to be released")
        return False

    def _paste_via_hotkey(self):
        """
        Send Ctrl+V to paste clipboard content into the currently active application.

        Key insight: We DON'T need to switch apps!
        - User works in their editor/app
        - User presses hotkey (Ctrl+Alt+D)
        - Hotkey is detected even if terminal is in background
        - Script transcribes in background
        - User's app REMAINS the active app the whole time
        - We just send Ctrl+V to the active app

        Returns:
            True if successful, False otherwise
        """
        try:
            time.sleep(0.05)

            # Send Ctrl+V to the currently active application
            logger.debug("About to send Ctrl+V to active application")

            with self.keyboard_lock:
                keyboard.send('ctrl+v')
                logger.debug("Ctrl+V sent")

            return True

        except Exception as e:
            logger.error(f"Error sending Ctrl+V: {e}", exc_info=True)
            return False

    def _insert_text_via_clipboard(self, text: str) -> bool:
        """
        Insert text via clipboard with original content restoration (Windows version)

        Args:
            text: Text to insert

        Returns:
            True if successful, False otherwise
        """
        try:
            # Wait until user has released all modifier keys
            if not self._ensure_no_modifiers_pressed():
                logger.error("Cannot insert via clipboard - modifiers still pressed after timeout")
                return False

            # Save current clipboard content
            original_clipboard = None
            clipboard_had_non_text = False
            try:
                original_clipboard = pyperclip.paste()
                if original_clipboard is None or original_clipboard == "":
                    clipboard_had_non_text = True
                    logger.debug("Empty clipboard detected - might be non-text content")
                else:
                    logger.debug(f"Clipboard content saved ({len(original_clipboard)} chars)")
            except Exception as e:
                logger.warning(f"Could not read clipboard (might contain image): {e}")
                clipboard_had_non_text = True

            # Copy text to clipboard
            pyperclip.copy(text)
            logger.debug(f"Text copied to clipboard ({len(text)} chars): '{text[:50]}{'...' if len(text) > 50 else ''}'")

            # Delay to ensure clipboard is updated - longer if non-text was in clipboard
            if clipboard_had_non_text:
                logger.debug("Non-text clipboard detected, using longer delay for compatibility")
                time.sleep(0.2)  # 200ms for apps like Notepad++ to recognize change
            else:
                time.sleep(0.1)

            # Verify clipboard content
            try:
                clipboard_check = pyperclip.paste()
                if clipboard_check == text:
                    logger.debug(f"Clipboard verified: content matches ({len(clipboard_check)} chars)")
                else:
                    logger.warning(f"Clipboard mismatch! Expected {len(text)} chars, got {len(clipboard_check)} chars")
            except Exception as e:
                logger.warning(f"Could not verify clipboard: {e}")

            # Send Ctrl+V to the ACTIVE application
            paste_success = self._paste_via_hotkey()

            if not paste_success:
                logger.warning("Paste via hotkey failed")
                return False

            # Delay after insertion
            time.sleep(CLIPBOARD_RESTORE_DELAY)

            # Restore prior clipboard only when real text was saved; an empty
            # string means non-text/empty content, and copy("") would wipe the
            # transcript ~100 ms after Ctrl+V before it lands (#23).
            if original_clipboard:
                try:
                    pyperclip.copy(original_clipboard)
                    logger.debug("Original clipboard content restored")
                except Exception as e:
                    logger.warning(f"Could not restore clipboard: {e}")

            return True

        except Exception as e:
            logger.error(f"Error during clipboard insertion: {e}")
            # Fallback to keyboard.write
            try:
                logger.info("Trying fallback to keyboard.write()")
                with self.keyboard_lock:
                    keyboard.write(text)
                return True
            except Exception as e2:
                logger.error(f"Fallback also failed: {e2}")
                return False

    def _output_manager_thread(self):
        """Thread that handles sequential text output"""
        logger.info("Output manager thread started")

        while self.running:
            try:
                with self.output_condition:
                    # Wait until something needs to be done
                    while self.running:
                        # Check if next sequence is ready (positive sequence numbers)
                        if self.next_sequence_to_output in self.output_queue:
                            task = self.output_queue[self.next_sequence_to_output]
                            if task.is_complete:
                                # This sequence is ready!
                                del self.output_queue[self.next_sequence_to_output]
                                break

                        # Check for immediate tasks (negative sequence numbers)
                        immediate_tasks = [t for t in self.output_queue.values()
                                         if t.sequence_number < 0 and t.is_complete]
                        if immediate_tasks:
                            # Immediate tasks can always be output immediately
                            # Sort by sequence number (most recent first for negative numbers)
                            immediate_tasks.sort(key=lambda t: t.sequence_number, reverse=True)
                            task = immediate_tasks[0]
                            del self.output_queue[task.sequence_number]
                            break

                        # Nothing to do - wait with timeout
                        self.output_condition.wait(timeout=OUTPUT_QUEUE_TIMEOUT)

                        # Check if we should exit after timeout
                        if not self.running:
                            return
                    else:
                        # running is False
                        return

                    # Process task
                    if task.sequence_number < 0:  # Immediate task with negative sequence
                        logger.debug(f"Output manager: Processing immediate task (Seq: {task.sequence_number})")
                    else:
                        logger.debug(f"Output manager: Processing sequence {task.sequence_number}")
                        self.next_sequence_to_output += 1

                # Wait for key release if needed (for immediate inserts)
                if task.wait_for_key_release and task.trigger_keys:
                    logger.debug(f"Task {task.sequence_number} waiting for key release: {task.trigger_keys}")
                    max_wait = 2.0
                    start_time = time.time()

                    while time.time() - start_time < max_wait:
                        keys_pressed = False
                        for key in task.trigger_keys:
                            if is_key_pressed(key):
                                keys_pressed = True
                                break

                        if not keys_pressed:
                            logger.debug(f"All keys released for task {task.sequence_number}")
                            break

                        time.sleep(0.01)
                    else:
                        logger.warning(f"Timeout waiting for key release for task {task.sequence_number}")

                # Output text (outside of lock!)
                if task.transcript and not task.is_error:
                    # Check if auto-insert is disabled (text only saved for later)
                    if not task.auto_insert:
                        logger.debug(f"Text for sequence {task.sequence_number} processed but NOT inserted (auto_insert=False)")
                        # Call completion callback FIRST to show model status inline
                        if self.on_task_complete:
                            # Pass a flag so print_ready_status knows this is a Y-task
                            self.on_task_complete(is_ready_to_insert=True, char_count=len(task.transcript))
                        # Continue to next task (don't insert)
                        continue

                    try:
                        if task.use_clipboard:
                            # Clipboard insertion
                            success = self._insert_text_via_clipboard(task.transcript)
                            if success:
                                # Log insertion
                                if task.sequence_number < 0:
                                    logger.debug(f"Immediate text inserted via clipboard (Seq: {task.sequence_number}, {len(task.transcript)} chars)")
                                else:
                                    logger.debug(f"Text for sequence {task.sequence_number} inserted via clipboard")

                                # Press Enter if send_after_insert is enabled
                                if task.send_after_insert:
                                    logger.debug(f"Pressing Enter to send message (Seq: {task.sequence_number})")
                                    time.sleep(0.2)  # Short pause for safety
                                    pyautogui.press('return')
                                    logger.debug(f"Text sent via Enter key (Seq: {task.sequence_number})")

                                # Call completion callback if registered
                                if self.on_task_complete:
                                    self.on_task_complete()
                            else:
                                logger.error("Clipboard insertion failed")
                                print(f"ERROR: Clipboard insertion failed (Seq: {task.sequence_number})")
                        else:
                            # Keyboard typing using keyboard.write()
                            logger.debug("Typing text into active application using keyboard.write()")

                            # Wait until user has released all modifier keys
                            if not self._ensure_no_modifiers_pressed():
                                logger.error(f"Cannot insert sequence {task.sequence_number} - modifiers still pressed after timeout")
                                continue

                            with self.keyboard_lock:
                                keyboard.write(task.transcript)

                            # Log insertion
                            if task.sequence_number < 0:
                                logger.debug(f"Immediate text inserted (Seq: {task.sequence_number}, {len(task.transcript)} chars)")
                            else:
                                logger.debug(f"Text for sequence {task.sequence_number} inserted")

                            # Press Enter if send_after_insert is enabled
                            if task.send_after_insert:
                                logger.debug(f"Pressing Enter to send message (Seq: {task.sequence_number})")
                                time.sleep(0.2)  # Short pause for safety
                                pyautogui.press('return')
                                logger.debug(f"Text sent via Enter key (Seq: {task.sequence_number})")

                            # Call completion callback if registered
                            if self.on_task_complete:
                                self.on_task_complete()

                    except Exception as e:
                        logger.error(f"Error during insertion: {e}")
                        print(f"ERROR: Text insertion failed (Seq: {task.sequence_number}) - {e}")
                elif task.is_error:
                    logger.info(f"Skipping errored sequence {task.sequence_number}")
                    print(f"ERROR: Transcription failed (Seq: {task.sequence_number})")

            except Exception as e:
                logger.error(f"Error in output manager: {e}", exc_info=True)
                print(f"ERROR: Output manager exception - {e}")

        logger.info("Output manager thread stopped")

    def stop(self):
        """Stop the output manager"""
        logger.info("Stopping output manager...")
        self.running = False
        with self.output_condition:
            self.output_condition.notify()

        # Wait for thread to finish
        self.output_thread.join(timeout=2)
        logger.info("Output manager stopped")
