package ru.scalping.collector.history;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.util.List;

/**
 * Режим выгрузки истории: единственная задача приложения — записать CSV для Analyzer'а.
 *
 * <p>Включается профилем {@code history-dump}; стрим и запись метаданных инструментов в БД
 * при этом не запускаются ({@code stream.CollectorStartup} исключён профилем).
 *
 * <p>Коды возврата: {@code 0} — все инструменты выгружены, {@code 2} — часть инструментов
 * вернула пустую историю, {@code 3} — пусто у всех, {@code 1} — ошибка выгрузки.
 *
 * <p>{@link System#exit(int)} вызывается намеренно: после выгрузки приложение должно завершиться
 * (в профиле нет веб-сервера и стрима), а shutdown hook Spring закроет контекст и {@code InvestApi}.
 */
@Component
@Profile("history-dump")
public class HistoryDumpRunner implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(HistoryDumpRunner.class);

    private final HistoryDumpService dumpService;
    private final HistoryDumpProperties properties;

    public HistoryDumpRunner(HistoryDumpService dumpService, HistoryDumpProperties properties) {
        this.dumpService = dumpService;
        this.properties = properties;
    }

    @Override
    public void run(ApplicationArguments args) {
        int exitCode = executeDump();
        log.info("History dump finished, exit code {}", exitCode);
        System.exit(exitCode);
    }

    private int executeDump() {
        try {
            List<DumpReport> reports = dumpService.dump(properties);
            long empty = reports.stream().filter(DumpReport::isEmpty).count();
            reports.forEach(report -> log.info("Dump result: {}@{} bars={} file={}",
                    report.ticker(), report.classCode(), report.bars(), report.path()));
            if (empty == 0) {
                return 0;
            }
            return empty == reports.size() ? 3 : 2;
        } catch (IOException | RuntimeException e) {
            log.error("History dump failed: {}", e.getMessage(), e);
            return 1;
        }
    }
}
