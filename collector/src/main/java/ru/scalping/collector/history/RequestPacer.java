package ru.scalping.collector.history;

import java.time.Duration;
import java.util.concurrent.TimeUnit;
import java.util.function.LongConsumer;
import java.util.function.LongSupplier;

/**
 * Ограничитель частоты запросов к API при выгрузке истории.
 *
 * <p>Зачем: T-Invest ограничивает число unary-запросов в минуту (сервис котировок — 600,
 * песочница — 200), а выгрузка месяца минутных свечей — это ~300 запросов подряд. Без паузы
 * Collector получает {@code RESOURCE_EXHAUSTED} уже на второй бумаге (замер 2026-11-21:
 * 304 страницы за 45 с ≈ 400 req/min).
 *
 * <p>Класс намеренно не использует {@link System#nanoTime()} напрямую: часы и «усыпление»
 * передаются снаружи, поэтому интервалы проверяются в тестах без реальных ожиданий.
 */
final class RequestPacer {

    /** Интервал между запросами; 0 — без ограничения. */
    private final long intervalNanos;
    private final LongSupplier nanoClock;
    private final LongConsumer sleeper;

    /** Момент предыдущего запроса; {@code -1} — запросов ещё не было. */
    private long lastNanos = -1L;

    private RequestPacer(long intervalNanos, LongSupplier nanoClock, LongConsumer sleeper) {
        this.intervalNanos = intervalNanos;
        this.nanoClock = nanoClock;
        this.sleeper = sleeper;
    }

    /** Пейсер на реальных часах: не более {@code requestsPerMinute} запросов в минуту. */
    static RequestPacer perMinute(int requestsPerMinute) {
        return perMinute(requestsPerMinute, System::nanoTime, RequestPacer::sleepNanos);
    }

    /** Версия с подменяемыми часами и «усыплением» — для тестов. */
    static RequestPacer perMinute(int requestsPerMinute, LongSupplier nanoClock, LongConsumer sleeper) {
        if (requestsPerMinute < 1) {
            throw new IllegalArgumentException("requestsPerMinute must be positive: " + requestsPerMinute);
        }
        return new RequestPacer(Duration.ofMinutes(1).toNanos() / requestsPerMinute, nanoClock, sleeper);
    }

    /** Без ограничения: используется там, где пауза не нужна (тесты, одиночный запрос). */
    static RequestPacer unlimited() {
        return new RequestPacer(0L, System::nanoTime, nanos -> { });
    }

    /**
     * Выдержать паузу перед очередным запросом.
     *
     * <p>Первый вызов не ждёт: отсчёт начинается с него. Если между вызовами прошло больше
     * интервала, ожидания нет — накопленная «переработка» не превращается в кредит на будущее,
     * иначе после долгого ответа сервера пачка запросов ушла бы подряд.
     */
    void await() {
        long now = nanoClock.getAsLong();
        if (lastNanos < 0) {
            lastNanos = now;
            return;
        }
        long target = lastNanos + intervalNanos;
        if (target > now) {
            sleeper.accept(target - now);
            lastNanos = target;
        } else {
            lastNanos = now;
        }
    }

    private static void sleepNanos(long nanos) {
        try {
            TimeUnit.NANOSECONDS.sleep(nanos);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("history dump interrupted", interrupted);
        }
    }
}
