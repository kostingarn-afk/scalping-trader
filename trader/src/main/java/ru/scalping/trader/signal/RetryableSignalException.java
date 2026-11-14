package ru.scalping.trader.signal;

/**
 * Обработка сигнала прервана по инфраструктурной причине (БД недоступна).
 *
 * <p>Consumer не коммитит offset: сообщение должно быть перечитано, иначе сигнал
 * теряется (at-least-once). Для ошибок данных (нарушение FK) исключение не бросается —
 * повтор не поможет, сообщение пропускается.
 */
public class RetryableSignalException extends RuntimeException {

    public RetryableSignalException(String message, Throwable cause) {
        super(message, cause);
    }
}
