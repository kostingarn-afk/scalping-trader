package ru.scalping.trader.recovery;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Component;
import ru.scalping.trader.config.TinkoffConfig;
import ru.scalping.trader.position.Position;
import ru.tinkoff.piapi.core.InvestApi;

import java.time.Instant;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;

/**
 * Состояние биржи для recovery через T-Invest SDK (задача 4.10).
 *
 * <p>{@code GetPositions} не отдаёт цену входа, текущую цену, PnL и идентификаторы
 * защитных ордеров, поэтому в {@link Position} эти поля заполняются нулями/{@code null},
 * а не восстанавливаются догадкой.
 */
@Component
public class TinkoffExchangeStateProvider implements ExchangeStateProvider {

    private static final Logger log = LoggerFactory.getLogger(TinkoffExchangeStateProvider.class);

    private final InvestApi investApi;
    private final String accountId;

    public TinkoffExchangeStateProvider(InvestApi investApi,
                                        @Qualifier(TinkoffConfig.TRADING_ACCOUNT_ID_BEAN) String accountId) {
        this.investApi = investApi;
        this.accountId = accountId;
    }

    @Override
    public Collection<Position> loadOpenPositions() {
        List<Holding> holdings = loadHoldings();
        List<Position> positions = new ArrayList<>(holdings.size());
        for (Holding holding : holdings) {
            if (holding.lots() == 0) {
                continue;
            }
            positions.add(new Position(
                    holding.figi(),
                    (int) holding.lots(),
                    0.0,
                    0.0,
                    0.0,
                    Instant.now(),
                    null,
                    0.0,
                    0.0,
                    null,
                    null));
        }
        log.info("Loaded {} open positions from T-Invest (sandbox={})", positions.size(), investApi.isSandboxMode());
        return positions;
    }

    /**
     * Песочница и prod отдают разные модели одного и того же ответа {@code PositionsResponse}:
     * {@code PositionsSecurities} и {@code SecurityPosition} соответственно. Приводим их к общей форме.
     */
    private List<Holding> loadHoldings() {
        if (investApi.isSandboxMode()) {
            return investApi.getSandboxService().getPositionsSync(accountId).getSecuritiesList().stream()
                    .map(security -> new Holding(security.getFigi(), security.getBalance()))
                    .toList();
        }
        return investApi.getOperationsService().getPositionsSync(accountId).getSecurities().stream()
                .map(security -> new Holding(security.getFigi(), security.getBalance()))
                .toList();
    }

    @Override
    public int tradingStatus(String figi) {
        try {
            return investApi.getMarketDataService().getTradingStatusSync(figi).getTradingStatusValue();
        } catch (RuntimeException e) {
            log.warn("Failed to get trading status: figi={}: {}", figi, e.getMessage());
            return -1;
        }
    }

    private record Holding(String figi, long lots) {
    }
}
