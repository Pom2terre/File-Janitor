"""Infrastructure générique pour exécuter du travail hors du thread GUI."""

from __future__ import annotations

import traceback
from concurrent.futures import CancelledError
from collections.abc import Callable
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    """Signaux émis pendant le cycle de vie d'un worker."""

    result = Signal(object)
    error = Signal(object)
    progress = Signal(object)
    cancelled = Signal()
    finished = Signal()


_NO_VALUE = object()


class Worker(QRunnable):
    """Adapte une fonction Python synchrone à un ``QThreadPool`` Qt."""

    def __init__(
        self,
        function: Callable[..., Any],
        *args: Any,
        progress_kwarg: str | None = None,
        cancel_kwarg: str | None = None,
        return_result_on_cancel: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self._function = function
        self._args = args
        self._kwargs = kwargs
        self._progress_kwarg = progress_kwarg
        self._cancel_kwarg = cancel_kwarg
        self._return_result_on_cancel = return_result_on_cancel
        self.signals = WorkerSignals()
        self._cancelled = Event()
        self._suppress_signals = Event()

    def cancel(self) -> None:
        """Annule silencieusement le worker, notamment pendant la fermeture."""

        self._cancelled.set()
        self._suppress_signals.set()

    def request_cancel(self) -> None:
        """Demande une annulation coopérative observable par la GUI."""

        self._cancelled.set()

    def _emit(self, signal: Any, value: object = _NO_VALUE) -> None:
        """Émet un signal tant que le worker n'a pas été annulé."""

        if self._suppress_signals.is_set():
            return
        try:
            if value is _NO_VALUE:
                signal.emit()
            else:
                signal.emit(value)
        except RuntimeError:
            # Le QObject de signaux peut déjà avoir été détruit pendant la
            # fermeture de l'application. Le travail est alors simplement
            # laissé se terminer sans publier de résultat tardif.
            return

    @Slot()
    def run(self) -> None:
        """Exécute la fonction et publie son résultat ou son exception."""

        try:
            kwargs = dict(self._kwargs)
            if self._progress_kwarg is not None:
                kwargs[self._progress_kwarg] = (
                    lambda value: self._emit(self.signals.progress, value)
                )
            if self._cancel_kwarg is not None:
                kwargs[self._cancel_kwarg] = self._cancelled.is_set
            result = self._function(*self._args, **kwargs)
        except CancelledError:
            self._emit(self.signals.cancelled)
        except Exception as exc:
            formatted_traceback = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
            self._emit(self.signals.error, (exc, formatted_traceback))
        else:
            if self._cancelled.is_set() and not self._return_result_on_cancel:
                self._emit(self.signals.cancelled)
            else:
                self._emit(self.signals.result, result)
        finally:
            self._emit(self.signals.finished)
