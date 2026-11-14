package ru.scalping.collector.instrument;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import ru.tinkoff.piapi.contract.v1.Future;
import ru.tinkoff.piapi.contract.v1.Quotation;
import ru.tinkoff.piapi.contract.v1.Share;
import ru.tinkoff.piapi.core.InvestApi;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Загрузка метаданных инструмента через T-Invest {@code InstrumentsService}.
 *
 * <p>Формат входа: {@code "SBER"} (автоопределение класса) или {@code "SiZ6@SPBFUT"}
 * (явный класс). Без явного класса перебираются акция {@code TQBR}, фьючерс
 * {@code SPBFUT} и его историческое имя {@code FORTS}.
 *
 * <p>Регистр тикера сохраняется: фьючерсные тикеры MOEX регистрозависимы
 * ({@code SiZ6}), акционные — нет. Если тикер задан не в целевом регистре,
 * выполняется повторная попытка в верхнем регистре.
 */
@Component
public class TinkoffInstrumentLoader implements InstrumentLoader {

    private static final Logger log = LoggerFactory.getLogger(TinkoffInstrumentLoader.class);

    private static final String SHARE_CLASS_CODE = "TQBR";
    /** Класс фьючерсов MOEX в T-Invest API (не «FORTS» — он не резолвится). */
    private static final String FUTURE_CLASS_CODE = "SPBFUT";
    private static final List<String> FUTURE_CLASS_CODES = List.of("SPBFUT", "FORTS");
    private static final String CLASS_CODE_SEPARATOR = "@";
    private static final int DEFAULT_TIER = 0;

    private final InvestApi investApi;

    public TinkoffInstrumentLoader(InvestApi investApi) {
        this.investApi = investApi;
    }

    @Override
    public InstrumentMeta loadByTicker(String ticker) {
        if (ticker == null || ticker.isBlank()) {
            throw new IllegalArgumentException("Instrument ticker must not be blank");
        }
        String reference = ticker.trim();
        int separator = reference.indexOf(CLASS_CODE_SEPARATOR);
        if (separator >= 0) {
            String plainTicker = reference.substring(0, separator);
            String classCode = reference.substring(separator + 1).toUpperCase(Locale.ROOT);
            if (plainTicker.isBlank() || classCode.isBlank()) {
                throw new IllegalArgumentException(
                        "Invalid instrument reference '" + ticker + "', expected TICKER or TICKER@CLASS_CODE");
            }
            return loadByTickerAndClass(plainTicker, classCode);
        }
        return loadAutoDetected(reference);
    }

    private InstrumentMeta loadAutoDetected(String ticker) {
        List<String> failures = new ArrayList<>(4);
        InstrumentMeta meta = tryCandidates(ticker, failures);
        if (meta == null) {
            String upper = ticker.toUpperCase(Locale.ROOT);
            if (!upper.equals(ticker)) {
                meta = tryCandidates(upper, failures);
            }
        }
        if (meta == null) {
            throw new IllegalStateException(
                    "Failed to load instrument '" + ticker + "' from T-Invest — attempts: "
                            + String.join("; ", failures));
        }
        return meta;
    }

    /** @return метаданные или null, если ни один класс не подошёл (ошибки копятся в {@code failures}). */
    private InstrumentMeta tryCandidates(String ticker, List<String> failures) {
        try {
            return loadShare(ticker, SHARE_CLASS_CODE);
        } catch (RuntimeException e) {
            failures.add("share@" + SHARE_CLASS_CODE + ": " + e.getMessage());
        }
        for (String classCode : FUTURE_CLASS_CODES) {
            try {
                return loadFuture(ticker, classCode);
            } catch (RuntimeException e) {
                failures.add("future@" + classCode + ": " + e.getMessage());
            }
        }
        return null;
    }

    private InstrumentMeta loadByTickerAndClass(String ticker, String classCode) {
        return isFutureClass(classCode) ? loadFuture(ticker, classCode) : loadShare(ticker, classCode);
    }

    private static boolean isFutureClass(String classCode) {
        return FUTURE_CLASS_CODES.contains(classCode);
    }

    private InstrumentMeta loadShare(String ticker, String classCode) {
        Share share = investApi.getInstrumentsService().getShareByTickerSync(ticker, classCode);
        if (share == null || share.getFigi().isBlank()) {
            throw new IllegalStateException("share not found");
        }
        log.debug("Loaded share {}@{} figi={}", ticker, classCode, share.getFigi());
        return new InstrumentMeta(
                share.getFigi(),
                share.getTicker(),
                share.getClassCode(),
                share.getName(),
                share.getLot(),
                toDouble(share.hasMinPriceIncrement() ? share.getMinPriceIncrement() : null),
                share.getCurrency(),
                share.getApiTradeAvailableFlag(),
                share.getTradingStatusValue(),
                DEFAULT_TIER);
    }

    private InstrumentMeta loadFuture(String ticker, String classCode) {
        Future future = investApi.getInstrumentsService().getFutureByTickerSync(ticker, classCode);
        if (future == null || future.getFigi().isBlank()) {
            throw new IllegalStateException("future not found");
        }
        log.debug("Loaded future {}@{} figi={}", ticker, classCode, future.getFigi());
        return new InstrumentMeta(
                future.getFigi(),
                future.getTicker(),
                future.getClassCode(),
                future.getName(),
                future.getLot(),
                toDouble(future.hasMinPriceIncrement() ? future.getMinPriceIncrement() : null),
                future.getCurrency(),
                future.getApiTradeAvailableFlag(),
                future.getTradingStatusValue(),
                DEFAULT_TIER);
    }

    /** {@code Quotation} → {@code double}: {@code units + nano / 1e9}. */
    private static double toDouble(Quotation quotation) {
        if (quotation == null) {
            return 0d;
        }
        return quotation.getUnits() + quotation.getNano() / 1_000_000_000d;
    }
}
