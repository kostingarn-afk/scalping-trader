package ru.scalping.trader.metrics;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.stereotype.Component;
import ru.scalping.trader.position.PositionStore;

import java.util.concurrent.atomic.AtomicLong;

/**
 * Метрики Trader (задача 4.13):
 * orders_placed_total, orders_rejected_total, pnl_realized_total, pnl_unrealized,
 * positions_open, circuit_breaker_active.
 */
@Component
public class TraderMetrics {

    private final Counter ordersPlaced;
    private final Counter ordersRejected;
    private final AtomicLong pnlRealized = new AtomicLong(0);   // в копейках для целочисленности
    private final AtomicLong pnlUnrealized = new AtomicLong(0);
    private final AtomicLong positionsOpen = new AtomicLong(0);
    private final AtomicLong circuitBreakerActive = new AtomicLong(0);

    public TraderMetrics(MeterRegistry registry, PositionStore positionStore) {
        this.ordersPlaced = Counter.builder("orders_placed_total")
                .description("Total orders placed").register(registry);
        this.ordersRejected = Counter.builder("orders_rejected_total")
                .description("Total orders rejected").register(registry);
        Gauge.builder("pnl_realized_total", pnlRealized, AtomicLong::doubleValue)
                .description("Realized PnL").register(registry);
        Gauge.builder("pnl_unrealized", pnlUnrealized, AtomicLong::doubleValue)
                .description("Unrealized PnL").register(registry);
        Gauge.builder("positions_open", positionsOpen, AtomicLong::doubleValue)
                .description("Open positions count").register(registry);
        Gauge.builder("circuit_breaker_active", circuitBreakerActive, AtomicLong::doubleValue)
                .description("PnL Circuit Breaker active (1/0)").register(registry);
    }

    public void orderPlaced() { ordersPlaced.increment(); }
    public void orderRejected() { ordersRejected.increment(); }
    public void pnlRealized(double pnl) { pnlRealized.addAndGet((long) (pnl * 100)); }
    public void pnlUnrealized(double pnl) { pnlUnrealized.set((long) (pnl * 100)); }
    public void positionsOpen(int count) { positionsOpen.set(count); }
    public void circuitBreakerActive(boolean active) { circuitBreakerActive.set(active ? 1 : 0); }
}
