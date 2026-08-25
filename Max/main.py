"""MAX transport module for CryptoLayer.

Implements the CryptoLayer ``BaseModule`` contract (module
``base_module`` from ``cryptolayer-module-interface``): transport of text
messages through a personal MAX account via the unofficial user-API
library ``vkmax`` (see ``docs/vkmax-contract.md`` — source of truth for
all vkmax adapter names, pinned in task 2).

ADDRESSING RULE (refactored: Chat ID is Peer ID):
    The dialog address is SOLELY the ``user_id``/Peer ID passed to
    ``init(creds, user_id)`` — the ``Chat ID`` credential no longer exists.
    Credentials hold only auth (Token, Device ID) and pacing; the peer
    field is the single source of truth for the dialog address.

Session adapter (task 4): ``create_session(ingester)`` runs a dedicated
daemon thread hosting an asyncio event loop (ProactorEventLoop on Windows
is fine — contract §12), logs in by token inside that loop and stores a
session-holder dict consumed by the nested Sender/Listener.

Sender (task 6): ``send(text)`` is ALWAYS non-blocking — it puts into a
bounded ``queue.Queue(maxsize=1000)`` via ``put_nowait()`` (Full → warning
+ immediate drop; the CryptoLayer kernel transport retries packets itself).
A daemon worker thread takes items one by one, waits the human-pacing
pause through the SINGLE interruptible primitive ``stop_event.wait(
random.uniform(min_pause, max_pause))`` (no ``time.sleep`` anywhere),
then dispatches ``send_message(client, chat_id, text)`` into the session
loop under ``asyncio.wait_for`` (contract §9/§7). Errors are classified:
transient (network/timeout/None-result/flood markers) → exponential
backoff base 5 s cap 120 s, retry while the stop_event stays clear;
terminal (auth/permission/bad-chat-id markers, or 5 consecutive failures
of one message when the text is unclassifiable) → ERROR log with an
actionable message («проверьте Token / Chat ID»), queue cleared, worker
stops WITHOUT touching stop_event. Typing emulation is NOT implemented —
contract §12 verdict NO (ADR-3a, documented in task 10).
Packet handling (Listener, task 5): ``set_packet_callback`` registers an
async handler taking ``(client, packet)`` (contract §3, SYNC registration).
opcode 128 packets for OUR chat from a FOREIGN sender have their text
handed to ``ingester`` through ONE dedicated FIFO worker thread — the
asyncio-loop thread never runs kernel code, so downstream processing
cannot stall the ws heartbeat. Own echo (sender == my_id, guaranteed by
task 4), foreign chats and non-128 opcodes are filtered out; any handler
exception is logged as a warning without killing the ws thread. vkmax has
NO internal reconnection (contract §12 verdict NO), so the listener
thread supervises the connection itself: on drop it waits 5→10→20→40 s
(cap 60 s), builds a NEW MaxClient via ``_build_client``, reconnects and
logs in again; after 5 consecutive failed reconnects it logs a terminal
ERROR («MAX connection lost permanently») and quietly exits. ``listen()``
is a blocking call suitable for ``Thread(target=listen)``; it returns as
soon as the shared ``stop_event`` is set.
"""

import asyncio
import logging
import queue
import random
import threading
import time

from base_module import BaseModule, Credential

logger = logging.getLogger(__name__)

# Contract §7: vkmax invoke_method waits for the server answer with NO
# timeout at all — every call is therefore wrapped in asyncio.wait_for,
# and the cross-thread future gets its own belt-and-suspenders timeout.
_LOGIN_TIMEOUT_S = 30.0
_RESULT_TIMEOUT_S = 45.0
_THREAD_JOIN_TIMEOUT_S = 5.0
# Contract §6: disconnect is awaited through run_coroutine_threadsafe
# with its OWN bounded timeout (plan task 7).
_DISCONNECT_TIMEOUT_S = 10.0
# Humanized read receipts (contract §15): an incoming message is marked
# read roughly this many seconds after delivery — instant «read» would
# look bot-like. Monkeypatchable in tests.
_READ_RECEIPT_DELAY_S = 1.0

_DEFAULT_MIN_PAUSE_S = 2.0
_DEFAULT_MAX_PAUSE_S = 6.0


def _parse_pause(raw, default: float, label: str) -> float:
    """Parse a pause credential (float seconds).

    Empty/blank value → ``default``; unparsable garbage → warning log +
    ``default`` (pacing must never block session bring-up).
    """
    text = "" if raw is None else str(raw).strip()
    if not text:
        return default
    try:
        return float(text.replace(",", "."))
    except ValueError:
        logger.warning(
            "%s: %r is not a number; using default %.1f sec", label, text, default
        )
        return default


def _build_client(token: str, device_id: str):
    """Build a vkmax client (adapter factory, contract §13).

    Thin wrapper over ``vkmax.client.MaxClient()`` (constructor takes NO
    arguments — see docs/vkmax-contract.md §0/§1). This function is THE
    monkeypatch point for tests.

    NOTE: the ``vkmax`` import is deliberately LAZY (inside this body) so
    that importing ``Max.main`` never requires vkmax to be installed —
    discovery (module_manager CLI) imports every module's main.py, and a
    missing optional dependency must not break it.
    """
    from vkmax.client import MaxClient  # lazy import on purpose

    return MaxClient()


def _arm_online_presence(client):
    """Patch the client's keepalive to report an ACTIVE web client.

    Contract §14: vkmax's keepalive loop sends ``opcode 1 {"interactive":
    False}`` every 30 s (client.py:176-184) — backgrounded-client
    semantics, which is why the account shows «не в сети» to the peer.
    Replacing the INSTANCE method keeps the library's 30 s timing and
    task lifecycle but flips the flag to ``True`` («в сети») for as long
    as the module runs; on terminal shutdown the loop dies with the
    session and the account goes offline naturally. Timeout semantics
    mirror the original (15 s, warning on timeout); other errors are
    logged at debug — the Listener's own supervision handles reconnects.

    An immediate presence packet is also sent so the peer sees «в сети»
    without waiting up to 30 s for the next keepalive tick.
    """

    async def _interactive_keepalive():
        try:
            async with asyncio.timeout(15):
                await client.invoke_method(
                    opcode=1, payload={"interactive": True}
                )
        except asyncio.TimeoutError:
            logger.warning("MAX keepalive ping timed out")
        except Exception as exc:  # noqa: BLE001 — shutdown races are legal
            logger.debug(
                "MAX keepalive skipped: %s: %s", type(exc).__name__, exc
            )

    client._send_keepalive_packet = _interactive_keepalive
    # Fire-and-forget immediate presence so «в сети» appears at once.
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_interactive_keepalive())
    except RuntimeError:
        # No running loop (e.g. during tests with FakeClient) — keepalive
        # loop will still send the first tick within 30 s.
        pass


# --- Sender (task 6): pacing, error classification, bounded queue ---

_SEND_TIMEOUT_S = 45.0  # asyncio.wait_for around send_message (contract §7)
_WORKER_POLL_S = 0.1  # idle poll of the queue while waiting for work
_BACKOFF_BASE_S = 5.0  # exponential backoff base (plan task 6)
_BACKOFF_CAP_S = 120.0  # exponential backoff cap (plan task 6)
_MAX_CONSECUTIVE_FAILURES = 5  # unclassifiable errors → terminal after N

# Contract §8: vkmax raises GENERIC Exception(str) — no error classes.
# Classification is therefore text-based; terminal markers first, then
# transient, anything else is "unknown" and falls back to the
# consecutive-failure rule. Exact server texts get pinned at the live
# owner checkpoint (task 12); until then this stays conservative.
_TERMINAL_MARKERS = (
    "invalid token",
    "token",  # covers bad/missing/expired token
    "auth",
    "unauthorized",
    "permission",
    "denied",
    "forbidden",
    "not found",
    "nosuchchat",
    "chatid",  # nonexistent dialog address
)
_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "connectionclosed",
    "connection closed",
    "connectionreset",
    "connection refused",
    "flood",
    "ratelimit",
    "rate limit",
)


class _TransientSendError(Exception):
    """Internal: ``send_message`` returned None (= not sent, §7)."""


def _get_send_message():
    """Return the vkmax send function lazily (contract §9).

    Lazy import keeps ``import Max.main`` working without vkmax
    installed; cached after the first call. THE monkeypatch point for
    sender tests.
    """
    global _SEND_MESSAGE_FN
    if _SEND_MESSAGE_FN is None:
        from vkmax.functions.messages import send_message

        _SEND_MESSAGE_FN = send_message
    return _SEND_MESSAGE_FN


_SEND_MESSAGE_FN = None


# --- Listener (task 5): filtering, FIFO ingest, reconnect supervision ---

# Reconnect backoff schedule: 5 → 10 → 20 → 40 s, capped at 60 s
# (plan task 5); each pause waits through the interruptible stop_event.
_RECONNECT_BASE_S = 5.0
_RECONNECT_CAP_S = 60.0
_MAX_FAILED_RECONNECTS = 5  # consecutive failures → terminal ERROR + exit

# How often the supervising listen() thread re-checks ws liveness while
# the connection is healthy. Cheap: one done() call per second.
_SUPERVISOR_POLL_S = 1.0

# Bound of the ingester hand-off queue. The CryptoLayer kernel transport
# drops packets older than 5 minutes; at this module's human-paced rates
# a 5-minute window can never approach five digits of chunks, so overflow
# is only reachable when the kernel's ingester is wedged — exactly the
# case where an unbounded queue would eat memory without end. On Full we
# drop the NEWEST chunk (oldest stay queued): the delivered prefix stays
# contiguous, which is what kernel chunk-reassembly cares about.
_INGEST_QUEUE_MAXSIZE = 10000


def _classify_send_error(exc: BaseException) -> str:
    """Classify a send failure: ``"transient"`` / ``"terminal"`` /
    ``"unknown"``.

    Timeouts are transient by type regardless of message text (contract
    §7); string markers per §8 above; everything else is unknown and is
    retried only up to the consecutive-failure limit.
    """
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "transient"
    text = str(exc).lower()
    if any(marker in text for marker in _TERMINAL_MARKERS):
        return "terminal"
    if any(marker in text for marker in _TRANSIENT_MARKERS):
        return "transient"
    return "unknown"


class Max(BaseModule):
    """CryptoLayer transport module: messaging over personal MAX accounts."""

    @property
    def unique_id(self) -> str:
        return "max_user_1"

    @property
    def name(self) -> str:
        return "MAX"

    @property
    def description(self) -> str:
        return (
            "Транспорт через личный аккаунт MAX (неофициальный user-API, "
            "vkmax). ВНИМАНИЕ: нарушает ToS MAX"
        )

    expected_credentials = [
        Credential("Token", "…__oneme_auth из LocalStorage web.max.ru"),
        Credential("Device ID", "…__oneme_device_id"),
        Credential("Мин. пауза, сек", "пауза между отправками; пусто = 2"),
        Credential("Макс. пауза, сек", "пусто = 6"),
    ]

    class Sender(BaseModule.Sender):
        """Outgoing-message worker with pacing (task 6).

        The overridden ``__init__`` accepts the extra ``session`` holder
        (allowed by the CryptoLayer docs §5.2 п.8); it stores the holder
        and keeps the addressing rule from task 3.

        TYPING EMULATION: contract §12 verdict NO — vkmax has no
        typing/status opcode, so typing is NOT implemented and NOT sent
        before messages (decision recorded as ADR-3a in docs/DECISIONS.md,
        written in plan task 10).
        """

        # Bounded queue per plan task 6; class attr so tests can shrink it.
        _QUEUE_MAXSIZE = 1000

        def __init__(self, credentials, user_id, session):
            if not user_id:
                raise ValueError("user_id must be non-empty (Peer ID = Chat ID)")
            super().__init__(credentials, user_id)
            self.session = session
            self._queue = queue.Queue(maxsize=self._QUEUE_MAXSIZE)
            self._worker_stop = threading.Event()
            self._worker_finished = threading.Event()
            self._worker_thread = None

        def send(self, text: str):
            """Enqueue a message — ALWAYS non-blocking.

            Never sends directly: the paced worker owns the transport.
            On a full queue the message is dropped with a warning — the
            CryptoLayer kernel transport retries packets itself.
            """
            self._ensure_worker()
            try:
                self._queue.put_nowait(str(text))
            except queue.Full:
                logger.warning(
                    "MAX sender queue is full (%d); dropping message",
                    self._QUEUE_MAXSIZE,
                )

        # --- worker lifecycle -------------------------------------------------

        def request_stop(self):
            """Cooperative stop signal for the sender worker only.

            Task 7 wires the FULL graceful shutdown (poison pill + client
            disconnect) on top of this primitive; the shared module
            ``stop_event`` is intentionally NOT touched here.
            """
            self._worker_stop.set()

        def join_worker(self, timeout=None):
            """Join the worker thread if it was ever started."""
            thread = self._worker_thread
            if thread is not None:
                thread.join(timeout)

        @property
        def worker_stopped(self) -> bool:
            return self._worker_finished.is_set()

        def _ensure_worker(self):
            """Start the daemon worker lazily on the first send()."""
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self._worker_stop.clear()
            self._worker_finished.clear()
            self._worker_thread = threading.Thread(
                target=self._run_worker,
                name="max-sender-worker",
                daemon=True,
            )
            self._worker_thread.start()

        def _run_worker(self):
            stop_event = self.session["stop_event"]
            try:
                while not self._worker_stop.is_set():
                    must_exit = self._process_one(stop_event)
                    if must_exit:
                        break
            finally:
                self._worker_finished.set()

        def _process_one(self, stop_event) -> bool:
            """Take one queued message and send it with pacing/retries.

            Returns True when the worker must exit (stop requested or a
            terminal failure), False to keep looping.
            """
            try:
                text = self._queue.get(timeout=_WORKER_POLL_S)
            except queue.Empty:
                return False

            # Human-pacing jitter through the SINGLE interruptible
            # primitive (no time.sleep anywhere in waiting paths).
            pause = random.uniform(
                self.session["min_pause"], self.session["max_pause"]
            )
            if stop_event.wait(pause):
                logger.info(
                    "Sender: stop_event during pacing pause; dropping "
                    "message plus %d queued",
                    self._queue.qsize(),
                )
                self._clear_queue()
                return True

            backoff = _BACKOFF_BASE_S
            consecutive = 0
            while True:
                try:
                    result = self._send_via_loop(text)
                    if result is None:
                        raise _TransientSendError(
                            "send_message returned None "
                            "(server silence = not sent)"
                        )
                    logger.debug("Sender: message delivered")
                    return False  # success — per-message counter resets too
                except Exception as exc:  # noqa: BLE001 — vkmax raises generic
                    consecutive += 1
                    if isinstance(exc, _TransientSendError):
                        kind = "transient"  # None result = §7 «не отправлено»
                    else:
                        kind = _classify_send_error(exc)
                    terminal = kind == "terminal" or (
                        kind == "unknown"
                        and consecutive >= _MAX_CONSECUTIVE_FAILURES
                    )
                    if terminal:
                        logger.error(
                            "Sender: TERMINAL send failure after %d attempt(s) "
                            "(%s: %s) — остановка worker'а. Проверьте Token / "
                            "Chat ID в credentials модуля.",
                            consecutive,
                            type(exc).__name__,
                            exc,
                        )
                        self._clear_queue()
                        self.request_stop()
                        return True
                    delay = min(backoff, _BACKOFF_CAP_S)
                    logger.warning(
                        "Sender: transient send failure (%s: %s); retrying "
                        "in %.1f s (attempt %d)",
                        type(exc).__name__,
                        exc,
                        delay,
                        consecutive,
                    )
                    # Backoff is interruptible by the shared stop_event.
                    if stop_event.wait(delay):
                        logger.info(
                            "Sender: stop_event during backoff; dropping "
                            "message plus %d queued",
                            self._queue.qsize(),
                        )
                        self._clear_queue()
                        return True
                    backoff = min(backoff * 2, _BACKOFF_CAP_S)

        def _send_via_loop(self, text):
            """Dispatch one send into the session's asyncio loop.

            Contract §9/§7: ``send_message(client, chat_id, text)``
            (op 64), wrapped in ``asyncio.wait_for`` because vkmax's
            invoke_method has NO internal timeout.
            """
            session = self.session
            client = session["client"]
            loop = session["loop"]
            chat_id = session["chat_id"]
            send_message = _get_send_message()

            async def _call():
                return await asyncio.wait_for(
                    send_message(client, chat_id, text),
                    timeout=_SEND_TIMEOUT_S,
                )

            future = asyncio.run_coroutine_threadsafe(_call(), loop)
            return future.result(_RESULT_TIMEOUT_S + 1.0)

        def _clear_queue(self):
            """Drain every pending message without sending them."""
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    return

    class Listener(BaseModule.Listener):
        """Incoming-packet handler with filtering (task 5).

        Same overridden-``__init__`` contract as Sender; additionally
        holds ``ingester`` (via the base class) and takes its stop_event
        from the session holder (single shared shutdown signal).

        Threading model (plan task 5): vkmax dispatches the packet
        callback as an asyncio task on the session loop (contract §3) —
        arbitrary kernel code must NOT run there or the ws heartbeat
        stalls. The handler therefore only FILTERS and ENQUEUES; exactly
        ONE dedicated worker thread consumes the FIFO queue and calls
        ``ingester``. Multiple consumers are forbidden: kernel chunk
        ordering depends on delivery order.
        """

        def __init__(self, credentials, ingester, user_id, session):
            if not user_id:
                raise ValueError("user_id must be non-empty (Peer ID = Chat ID)")
            super().__init__(
                credentials, ingester, user_id, session["stop_event"]
            )
            self.session = session
            # Base Listener.__init__ does NOT keep credentials; reconnect
            # needs Token/Device ID, so keep our own normalized copy.
            self._credentials = [
                "" if c is None else str(c) for c in credentials
            ]
            self._ingest_queue = queue.Queue(maxsize=_INGEST_QUEUE_MAXSIZE)
            self._ingest_thread = None

        # --- registration + packet handling -----------------------------------

        def _register_on(self, client):
            """Register the packet callback — a SYNC call (contract §3).

            The callback MUST be an async function taking two positional
            arguments ``(client, packet)``; vkmax raises TypeError for
            sync functions and dispatches via ``asyncio.create_task`` on
            ANY non-pending-seq packet, so this handler catches its own
            exceptions (they would otherwise die silently in the task).
            """
            client.set_packet_callback(self._on_packet)

        async def _on_packet(self, client, packet):
            """Filter one pushed packet; NEVER raises outward.

            opcode != 128 and foreign chats are silently ignored; own
            echo is excluded by comparing ``payload.message.sender``
            against ``my_id`` (guaranteed present by task 4 — no
            degraded chat_id-only mode, or our own sends would loop);
            malformed payloads log a warning and are dropped.
            """
            try:
                if not isinstance(packet, dict):
                    logger.warning(
                        "Listener: non-dict packet of type %s dropped",
                        type(packet).__name__,
                    )
                    return
                if packet.get("opcode") != 128:
                    return  # not a message push — silently ignore
                payload = packet.get("payload")
                if not isinstance(payload, dict):
                    logger.warning(
                        "Listener: op=128 packet without payload dict dropped"
                    )
                    return
                if payload.get("chatId") != self.session["chat_id"]:
                    return  # foreign dialog — silently ignore
                message = payload.get("message")
                if not isinstance(message, dict) or "sender" not in message:
                    # "sender" path is live-unconfirmed until owner
                    # checkpoint (contract §4); delivering on schema
                    # drift would mask it — warn instead.
                    logger.warning(
                        "Listener: op=128 for our chat lacks "
                        "message/sender fields; packet dropped"
                    )
                    return
                if message["sender"] == self.session["my_id"]:
                    return  # own echo — excluded to break send loops
                text = message.get("text")
                if text is None:
                    logger.warning(
                        "Listener: op=128 message without text dropped"
                    )
                    return
                self._enqueue(str(text))
                # Humanized read receipt (contract §15): mark the message
                # read ~1 s after delivery so the peer sees «прочитано»
                # the way a human web client would produce it.
                self._schedule_read_mark(message["id"])
            except Exception:  # noqa: BLE001 — handler must never kill ws
                logger.warning(
                    "Listener: packet handling failed", exc_info=True
                )

        def _schedule_read_mark(self, message_id):
            """Fire one opcode-50 READ_MESSAGE ~_READ_RECEIPT_DELAY_S later.

            Contract §15 (docs/opcodes.md v11): payload
            ``{"type": "READ_MESSAGE", "chatId": …, "messageId": …,
            "mark": <epoch ms>}``. Runs as a task on the session loop (we
            are ON that loop inside _on_packet); failures are warnings —
            a missed read mark must never affect message delivery.
            """
            session = self.session
            client = session["client"]
            chat_id = session["chat_id"]

            async def _mark_read():
                await asyncio.sleep(_READ_RECEIPT_DELAY_S)
                try:
                    await asyncio.wait_for(
                        client.invoke_method(
                            opcode=50,
                            payload={
                                "type": "READ_MESSAGE",
                                "chatId": chat_id,
                                "messageId": str(message_id),
                                "mark": int(time.time() * 1000),
                            },
                        ),
                        timeout=_SEND_TIMEOUT_S,
                    )
                except Exception as exc:  # noqa: BLE001 — best effort
                    logger.warning(
                        "Listener: read-mark failed for message %s (%s: %s)",
                        message_id,
                        type(exc).__name__,
                        exc,
                    )

            asyncio.get_running_loop().create_task(_mark_read())

        def _enqueue(self, text: str):
            """Hand one filtered chunk to the ingest worker (non-blocking)."""
            try:
                self._ingest_queue.put_nowait(text)
            except queue.Full:
                # Drop the NEWEST chunk: the queued prefix stays
                # contiguous for kernel reassembly (see queue-bound note).
                logger.warning(
                    "Listener: ingest queue full (%d); dropping newest chunk",
                    _INGEST_QUEUE_MAXSIZE,
                )

        # --- single-consumer FIFO worker --------------------------------------

        def _start_ingest_worker(self):
            """Start THE dedicated ingest thread (idempotent).

            Single consumer BY DESIGN (plan task 5): kernel chunk order
            equals delivery order only with one consumer multiplying
            threads is forbidden.
            """
            if self._ingest_thread is not None and self._ingest_thread.is_alive():
                return
            self._ingest_thread = threading.Thread(
                target=self._run_ingest_worker,
                name="max-listener-ingest",
                daemon=True,
            )
            self._ingest_thread.start()

        def _run_ingest_worker(self):
            stop_event = self.session["stop_event"]
            while True:
                try:
                    item = self._ingest_queue.get(timeout=_WORKER_POLL_S)
                except queue.Empty:
                    if stop_event.is_set():
                        return
                    continue
                if item is None:  # poison pill (task 7 shutdown)
                    return
                try:
                    self.ingester(item)
                except Exception:  # noqa: BLE001 — downstream is foreign code
                    logger.warning(
                        "Listener: ingester raised; continuing", exc_info=True
                    )

        # --- connection supervision / reconnect -------------------------------

        @staticmethod
        def _ws_dead(client) -> bool:
            """True when the client's receive loop has ended.

            Contract §12: without a reconnect hook vkmax's recv-loop dies
            SILENTLY on a ws drop, and §6 disconnect() cancels
            ``_recv_task`` — either way a finished ``_recv_task`` means
            the connection is gone. Duck-typed so fakes need no asyncio.
            """
            recv = getattr(client, "_recv_task", None)
            return recv is None or recv.done()

        def _reconnect(self):
            """Build a NEW client, connect and log in again.

            Contract §6/§13: reconnect = fresh MaxClient() per cycle
            (clean ``_pending``/``_seq``), NOT reuse of the dead one.
            Raises on any failure — the caller counts consecutive fails.
            """
            token = self._credentials[0].strip()
            device_id = self._credentials[1].strip()
            client = _build_client(token, device_id)

            async def _bring_up():
                await client.connect()
                await asyncio.wait_for(
                    client.login_by_token(token, device_id),
                    timeout=_LOGIN_TIMEOUT_S,
                )

            asyncio.run_coroutine_threadsafe(
                _bring_up(), self.session["loop"]
            ).result(_RESULT_TIMEOUT_S + 1.0)
            _arm_online_presence(client)
            self._register_on(client)
            return client

        def listen(self):
            """Blocking receiver entry point (for ``Thread(target=listen)``).

            Registers the packet callback, starts the single ingest
            worker, then supervises the connection: while healthy it
            merely waits on the shared stop_event (the plan-mandated
            blocking-stub behaviour); on a detected drop it reconnects
            with 5→10→20→40 s (cap 60 s) pauses. After 5 consecutive
            FAILED reconnect attempts it logs a terminal ERROR («MAX
            connection lost permanently») and quietly returns — the core
            detects the death via its own ping timeout. Returns as soon
            as stop_event is set.
            """
            session = self.session
            stop_event = session["stop_event"]
            self._start_ingest_worker()
            client = session["client"]
            self._register_on(client)
            logger.info(
                "Listener: receiving packets for chat %s", session["chat_id"]
            )

            delay = _RECONNECT_BASE_S
            failed_reconnects = 0
            while not stop_event.is_set():
                if not self._ws_dead(client):
                    stop_event.wait(_SUPERVISOR_POLL_S)
                    continue
                if stop_event.is_set():
                    break
                logger.warning(
                    "MAX websocket connection lost; reconnecting in %.0f s",
                    delay,
                )
                if stop_event.wait(delay):
                    break
                delay = min(delay * 2, _RECONNECT_CAP_S)
                try:
                    client = self._reconnect()
                except Exception as exc:  # noqa: BLE001 — vkmax generic
                    failed_reconnects += 1
                    logger.warning(
                        "Listener: reconnect attempt %d/%d failed (%s: %s)",
                        failed_reconnects,
                        _MAX_FAILED_RECONNECTS,
                        type(exc).__name__,
                        exc,
                    )
                    if failed_reconnects >= _MAX_FAILED_RECONNECTS:
                        logger.error(
                            "MAX connection lost permanently после %d "
                            "неудачных переподключений — проверьте Token / "
                            "Device ID и доступность сети; поток приёма "
                            "остановлен (ядро обнаружит таймаут пинга).",
                            failed_reconnects,
                        )
                        return
                    continue
                failed_reconnects = 0
                delay = _RECONNECT_BASE_S
                # Swap into the holder so Sender also uses the live client.
                session["client"] = client
                logger.info("Listener: MAX reconnected")

    def _stop_loop_thread(self) -> None:
        """Stop the dedicated event-loop thread.

        Used on the create_session failure paths here; the full graceful
        shutdown (worker stop + client disconnect first) arrives in task 7
        on top of this primitive.
        """
        loop = getattr(self, "_loop", None)
        thread = getattr(self, "_loop_thread", None)
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass  # loop already closed between check and call
        if thread is not None and thread.is_alive():
            thread.join(timeout=_THREAD_JOIN_TIMEOUT_S)
        if loop is not None and not loop.is_closed():
            loop.close()

    def create_session(self, ingester):
        """Create the MAX session and wire Sender/Listener (task 4).

        Contract: takes EXACTLY one argument (``ingester``).

        Steps: parse credentials → spawn a daemon thread running a fresh
        asyncio event loop → inside that loop build the vkmax client,
        connect and log in by token → read our own id from the login
        response → store the session-holder dict → instantiate the nested
        Listener (holding ``ingester``) and Sender.

        Login failure of any kind raises ``RuntimeError("MAX login "
        "failed: проверьте Token и Device ID")`` with the original error
        as ``__cause__``. An unreadable own id is a separate hard failure
        (echo filtering in task 5 depends on it): RuntimeError, no
        degraded mode.
        """
        credentials = list(getattr(self, "credentials", None) or [])
        if len(credentials) < 2:
            raise ValueError(
                "module has no credentials: call init(creds, user_id) first"
            )
        token = str(credentials[0]).strip()
        device_id = str(credentials[1]).strip()

        user_id = getattr(self, "user_id", "")
        if not user_id:
            # Fail BEFORE spawning any thread — no resources to leak.
            raise ValueError("user_id must be non-empty (Peer ID = Chat ID)")

        raw_chat_id = str(user_id).strip()
        try:
            chat_id = int(raw_chat_id)
        except ValueError as exc:
            raise ValueError(
                f"Chat ID (Peer ID) must be an integer, got {raw_chat_id!r}"
            ) from exc

        min_pause = _parse_pause(
            credentials[2] if len(credentials) > 2 else "",
            _DEFAULT_MIN_PAUSE_S,
            "Мин. пауза, сек",
        )
        max_pause = _parse_pause(
            credentials[3] if len(credentials) > 3 else "",
            _DEFAULT_MAX_PAUSE_S,
            "Макс. пауза, сек",
        )

        # --- threaded asyncio bridge (Telegram-module pattern) ---
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever,
            name="max-asyncio-loop",
            daemon=True,
        )
        self._loop_thread.start()

        def _run(coro):
            return asyncio.run_coroutine_threadsafe(
                coro, self._loop
            ).result(_RESULT_TIMEOUT_S)

        async def _login():
            # Contract §2: connect() MUST precede login (@ensure_connected
            # raises otherwise); login_by_token RETURNS the full server
            # response (§1.1/§2), from which the own id is read below.
            client = _build_client(token, device_id)
            await client.connect()
            response = await asyncio.wait_for(
                client.login_by_token(token, device_id),
                timeout=_LOGIN_TIMEOUT_S,
            )
            # Contract §14: report «в сети» for as long as the module runs
            # (the library's own keepalive would report False every 30 s).
            _arm_online_presence(client)
            return client, response

        try:
            client, login_response = _run(_login())
        except Exception as exc:
            self._stop_loop_thread()
            raise RuntimeError(
                "MAX login failed: проверьте Token и Device ID"
            ) from exc

        # Own id — HARD requirement for sender-echo filtering (task 5).
        # Contract §5 (LIVE-CONFIRMED 2026-08-23, scripts/dump_profile.py):
        # client.me does NOT exist; own id lives at
        # payload.profile.contact.id on the live server (corroborated by
        # chat.owner / lastMessage.sender / participants keys). Legacy
        # payload.profile.id kept as a defensive fallback. Unreadable
        # through BOTH paths → RuntimeError.
        my_id = None
        try:
            profile = login_response["payload"]["profile"]
            my_id = profile["contact"]["id"]
        except (KeyError, TypeError):
            pass
        if my_id is None:
            try:
                my_id = login_response["payload"]["profile"]["id"]  # legacy
            except (KeyError, TypeError) as exc:
                self._stop_loop_thread()
                raise RuntimeError(
                    "MAX login failed: my id unavailable "
                    "(payload.profile.contact.id missing in login response)"
                ) from exc

        self._session = {
            "client": client,
            "loop": self._loop,
            "chat_id": chat_id,
            "my_id": my_id,
            "stop_event": self.stop_event,
            "min_pause": min_pause,
            "max_pause": max_pause,
        }
        self.listener = self.Listener(
            list(self.credentials), ingester, user_id, self._session
        )
        self.sender = self.Sender(list(self.credentials), user_id, self._session)

        # The kernel wires ONLY the sender (crypto_layer.init_module);
        # starting the receiver is the MODULE's job (VK-module precedent:
        # Thread(target=listener.listen).start()). Without this thread the
        # packet callback is never registered and nothing is ever received.
        self._listener_thread = threading.Thread(
            target=self.listener.listen, name="max-listener", daemon=True
        )
        self._listener_thread.start()

        # Task 7: watcher thread armed LAST — the shared stop_event is the
        # single shutdown signal (docs §3.4: the core sends DISCONNECT,
        # the module just quietly stops).
        self._shutdown_done = threading.Event()
        self._stop_watcher = threading.Thread(
            target=self._watch_stop, name="max-stop-watcher", daemon=True
        )
        self._stop_watcher.start()

    # --- graceful shutdown (task 7) ----------------------------------------

    def _watch_stop(self):
        """Block on the shared stop_event, then run the full shutdown."""
        try:
            self.stop_event.wait()
            self._shutdown()
        except Exception:  # noqa: BLE001 — a watcher must never crash loud
            logger.error(
                "Stop-watcher failed", exc_info=True
            )

    def stop(self):
        """Public graceful-shutdown entry point.

        Sets the shared ``stop_event`` (so Listener/worker loops notice)
        and runs the same idempotent teardown the watcher runs. Calling
        it a second time is a no-op.
        """
        if getattr(self, "_session", None) is None:
            return  # session never brought up — nothing to stop
        self.stop_event.set()
        self._shutdown()
        # The watcher may have won the idempotence race and be doing the
        # teardown right now — wait for it so stop() always returns
        # AFTER shutdown has fully completed.
        watcher = getattr(self, "_stop_watcher", None)
        if watcher is not None and watcher is not threading.current_thread():
            watcher.join(timeout=_THREAD_JOIN_TIMEOUT_S)

    def _shutdown(self):
        """Idempotent teardown: workers first, THEN client disconnect.

        Order matters (contract §6): vkmax's ``disconnect()`` cancels
        ``_recv_task`` but strands pending ``invoke_method`` futures
        forever — sends must be prevented BEFORE disconnecting. Every
        step is wrapped in try/except because disconnect is NOT
        idempotent and raises when keepalive is not running.
        """
        done = getattr(self, "_shutdown_done", None)
        if done is None:
            done = self._shutdown_done = threading.Event()
        if done.is_set():
            return  # IDEMPOTENCE: second stop is a no-op
        done.set()

        sender = self.sender
        listener = self.listener
        if sender is not None:
            try:
                # Stop the pacing worker FIRST and drop everything still
                # queued — nothing new may hit the transport from now on.
                sender.request_stop()
                sender._clear_queue()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Shutdown: stopping sender worker failed", exc_info=True
                )

        if listener is not None:
            try:
                listener._ingest_queue.put_nowait(None)  # poison pill
            except queue.Full:  # pragma: no cover — worker drains promptly
                pass

        session = self._session
        loop = session["loop"]
        client = session["client"]

        # Idempotent wrapper around the NOT-idempotent vkmax disconnect.
        # Guard BEFORE constructing the coroutine: on a CLOSED loop
        # run_coroutine_threadsafe raises before scheduling (leaving the
        # coroutine unawaited), and on a STOPPED-but-open loop it silently
        # queues work that never runs — hanging the future to its timeout.
        if not loop.is_closed() and loop.is_running():
            try:

                async def _disconnect():
                    await client.disconnect()

                coro = _disconnect()
                try:
                    asyncio.run_coroutine_threadsafe(coro, loop).result(
                        _DISCONNECT_TIMEOUT_S
                    )
                except BaseException:
                    # Belt-and-suspenders: never leak an unawaited
                    # coroutine (a running one cannot be closed — ignore).
                    try:
                        coro.close()
                    except RuntimeError:
                        pass
                    raise
            except Exception:  # noqa: BLE001 — §6: raises in many states
                logger.warning(
                    "Shutdown: client.disconnect() failed "
                    "(keepalive already stopped or connection gone)",
                    exc_info=True,
                )
        else:
            logger.warning(
                "Shutdown: session loop already closed or not running; "
                "skipping client.disconnect() (connection is gone anyway)"
            )

        # Cancel whatever futures/tasks are still pending on the loop
        # (stranded invoke_method futures would otherwise keep running).
        def _cancel_pending():
            for task in asyncio.all_tasks(loop):
                task.cancel()

        try:
            loop.call_soon_threadsafe(_cancel_pending)
        except RuntimeError:
            pass  # loop already closed between check and call

        # Join module threads with timeout, then tear down the loop
        # thread via the task-4 primitive (stop + join + close).
        if sender is not None:
            sender.join_worker(timeout=_THREAD_JOIN_TIMEOUT_S)
        listener_thread = getattr(self, "_listener_thread", None)
        if listener_thread is not None and listener_thread.is_alive():
            listener_thread.join(timeout=_THREAD_JOIN_TIMEOUT_S)
        if listener is not None:
            ingest_thread = listener._ingest_thread
            if ingest_thread is not None:
                ingest_thread.join(timeout=_THREAD_JOIN_TIMEOUT_S)
        self._stop_loop_thread()
        logger.info("MAX module stopped cleanly")
