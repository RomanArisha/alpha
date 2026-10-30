
#include <simpleble/SimpleBLE.h>
#include <windows.h>
#include <conio.h>

#include <iostream>
#include <iomanip>
#include <cmath>
#include <deque>
#include <map>
#include <set>
#include <string>
#include <string_view>
#include <chrono>
#include <atomic>
#include <mutex>
#include <vector>
#include <array>
#include <thread>
#include <cctype>
#include <utility>
#include <fstream>
#include <cstdint>

// ============================================================
// НАСТРОЙКИ
// ============================================================

// RSSI на расстоянии 1 метра.
// Значение нужно калибровать для конкретных устройств.
constexpr double RSSI_AT_1M = -66.0;

// Коэффициент потерь распространения сигнала.
constexpr double PATH_LOSS_EXPONENT = 2.5;

// Количество последних RSSI для сглаживания.
constexpr size_t HISTORY_SIZE = 5;

// Удаление устройства после отсутствия событий, секунд.
constexpr int STALE_SECONDS = 60;

// Период обработки накопленных событий, миллисекунд.
constexpr int TOTAL_WINDOW_MS = 5000;

// Интервал проверки клавиатуры, миллисекунд.
constexpr int POLL_STEP_MS = 50;

// Имя CSV по умолчанию.
constexpr const char* DEFAULT_LOG_NAME = "ble_log.csv";

// Идентификатор сканера.
constexpr int SCANNER_ID = 0;

// Имена устройств, которые отслеживаются в режиме WORK.
constexpr std::array<std::string_view, 2> TARGETS = {
    "POCO X7 Pro",
    "Meeee"
};

// ============================================================
// СТРУКТУРЫ ДАННЫХ
// ============================================================

struct DeviceInfo {
    std::string name;
    std::deque<int> rssiHistory;

    std::chrono::steady_clock::time_point lastSeen{};
};

struct RawPacket {
    std::string mac;
    std::string name;
    int rssi = 0;

    // Время поступления события в Unix milliseconds.
    int64_t timestampUnixMs = 0;
};

// ============================================================
// ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
// ============================================================

// Очередь событий, полученных через SimpleBLE.
std::mutex g_queueMutex;
std::vector<RawPacket> g_rawPackets;

// Хранилище отслеживаемых устройств.
// Используется только основным потоком.
std::map<std::string, DeviceInfo> devices;

// Устройства, встретившиеся в текущем окне.
std::set<std::string> seenThisWindow;

// Путь к CSV.
std::string g_logPath;

// Управление программой.
std::atomic<bool> g_running{true};

// Запрос на включение/выключение логирования.
std::atomic<bool> g_logging{false};

// Количество событий, переданных колбэками в очередь.
std::atomic<uint64_t> g_receivedEvents{0};

// Максимальный размер очереди, замеченный при извлечении партии.
size_t g_maxBatchSize = 0;

// Состояние сканера.
enum class Mode {
    Work,
    Detect,
    Pause
};

std::atomic<Mode> g_mode{Mode::Work};

// CSV-файл используется только основным потоком.
std::ofstream g_logFile;

// ============================================================
// СРАВНЕНИЕ СТРОК БЕЗ УЧЁТА РЕГИСТРА
// ============================================================

bool iequals(std::string_view a, std::string_view b) {
    if (a.size() != b.size()) {
        return false;
    }

    for (size_t i = 0; i < a.size(); ++i) {
        const auto ca = static_cast<unsigned char>(a[i]);
        const auto cb = static_cast<unsigned char>(b[i]);

        if (std::tolower(ca) != std::tolower(cb)) {
            return false;
        }
    }

    return true;
}

// ============================================================
// ПРОВЕРКА ЦЕЛЕВЫХ УСТРОЙСТВ
// ============================================================

bool isTarget(const std::string& name, const std::string& mac) {
    for (const auto target : TARGETS) {
        if (iequals(name, target) || iequals(mac, target)) {
            return true;
        }
    }

    return false;
}

// ============================================================
// ВРЕМЯ UNIX В МИЛЛИСЕКУНДАХ
// ============================================================

int64_t currentUnixMs() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()
    ).count();
}

// ============================================================
// CSV: ЭКРАНИРОВАНИЕ ПОЛЕЙ
// ============================================================

std::string csvEscape(const std::string& value) {
    std::string result;
    result.reserve(value.size() + 2);

    bool needsQuotes = false;

    for (char c : value) {
        if (c == '"' || c == ',' || c == '\n' || c == '\r') {
            needsQuotes = true;
        }

        if (c == '"') {
            result += "\"\"";
        } else {
            result += c;
        }
    }

    if (needsQuotes) {
        result.insert(result.begin(), '"');
        result.push_back('"');
    }

    return result;
}

// ============================================================
// ПОЛУЧЕНИЕ СОБЫТИЙ SIMPLEBLE
// ============================================================

// Функция вызывается из BLE-колбэков.
//
// Внутри колбэка мы только извлекаем данные и кладём их в очередь.
// Здесь не выполняются вывод в консоль, расчёт расстояния
// и запись в файл.

void enqueuePacket(SimpleBLE::Peripheral p) {
    RawPacket pkt;

    pkt.mac = p.address();
    pkt.name = p.identifier();
    pkt.rssi = p.rssi();
    pkt.timestampUnixMs = currentUnixMs();

    {
        std::lock_guard<std::mutex> lock(g_queueMutex);

        g_rawPackets.push_back(std::move(pkt));
    }

    ++g_receivedEvents;
}

// ============================================================
// УПРАВЛЕНИЕ КЛАВИАТУРОЙ
// ============================================================

void handleKey(int key) {
    // Приводим ASCII-команды к нижнему регистру.
    if (key >= 'A' && key <= 'Z') {
        key = std::tolower(
            static_cast<unsigned char>(key)
        );
    }

    switch (key) {
        case 'p':
            g_mode = Mode::Pause;
            std::cout << "\nPause mode requested.\n";
            break;

        case 'd':
            g_mode = Mode::Detect;
            std::cout << "\nDetect mode requested.\n";
            break;

        case 'w':
            g_mode = Mode::Work;
            std::cout << "\nWork mode requested.\n";
            break;

        case 'b':
            g_logging = true;
            break;

        case 'e':
            g_logging = false;
            break;

        case 'q':
            g_running = false;
            break;

        default:
            break;
    }
}

// ============================================================
// ЗАПУСК ЛОГИРОВАНИЯ
// ============================================================

void startLogging() {
    std::cout
        << "Enter log file name ["
        << DEFAULT_LOG_NAME
        << "]: ";

    std::cout.flush();

    std::string name;
    std::getline(std::cin, name);

    if (name.empty()) {
        name = DEFAULT_LOG_NAME;
    }

    // Добавляем расширение, если оно не указано.
    if (name.find('.') == std::string::npos) {
        name += ".csv";
    }

    g_logFile.open(
        name,
        std::ios::out | std::ios::trunc
    );

    if (!g_logFile.is_open()) {
        std::cerr
            << "Cannot open file: "
            << name
            << "\n";

        g_logging = false;
        return;
    }

    g_logPath = name;

    g_logFile
        << "scanner_id,"
        << "timestamp_unix_ms,"
        << "mac,"
        << "name,"
        << "rssi\n";

    g_logFile.flush();

    std::cout
        << "Logging to "
        << g_logPath
        << "\n";
}

// ============================================================
// ОСТАНОВКА ЛОГИРОВАНИЯ
// ============================================================

void stopLogging() {
    if (!g_logFile.is_open()) {
        return;
    }

    g_logFile.flush();
    g_logFile.close();

    std::cout
        << "Log saved: "
        << g_logPath
        << "\n";
}

// ============================================================
// ЗАПИСЬ СОБЫТИЙ В CSV
// ============================================================

// Записывается каждая запись из batch.
// В отличие от прежнего варианта, здесь нет фильтрации по MAC
// и нет ограничения в одну строку на устройство за окно.

void writeBatchToCsv(const std::vector<RawPacket>& batch) {
    if (!g_logFile.is_open()) {
        return;
    }

    for (const auto& pkt : batch) {
        // Записываем только целевые устройства.
        if (!isTarget(pkt.name, pkt.mac)) {
            continue;
        }

        g_logFile
            << SCANNER_ID << ','
            << pkt.timestampUnixMs << ','
            << csvEscape(pkt.mac) << ','
            << csvEscape(pkt.name) << ','
            << pkt.rssi
            << '\n';
    }

    g_logFile.flush();

    if (!g_logFile) {
        std::cerr << "Error writing to log file.\n";
        stopLogging();
        g_logging = false;
    }
}


// ============================================================
// РАСЧЁТ РАССТОЯНИЯ ПО RSSI
// ============================================================

double calculateDistance(double rssi) {
    if (rssi == 0.0) {
        return -1.0;
    }

    return std::pow(
        10.0,
        (RSSI_AT_1M - rssi) /
            (10.0 * PATH_LOSS_EXPONENT)
    );
}

// ============================================================
// ОБНОВЛЕНИЕ ИСТОРИИ RSSI
// ============================================================

void updateDevice(
    const RawPacket& pkt,
    std::chrono::steady_clock::time_point now
) {
    auto& info = devices[pkt.mac];

    if (info.name.empty() && !pkt.name.empty()) {
        info.name = pkt.name;
    }

    // Если имя ранее было неизвестно, но теперь появилось,
    // обновляем его.
    if (!pkt.name.empty()) {
        info.name = pkt.name;
    }

    info.rssiHistory.push_back(pkt.rssi);

    while (info.rssiHistory.size() > HISTORY_SIZE) {
        info.rssiHistory.pop_front();
    }

    info.lastSeen = now;
}

// ============================================================
// УДАЛЕНИЕ УСТАРЕВШИХ УСТРОЙСТВ
// ============================================================

void removeStaleDevices(
    std::chrono::steady_clock::time_point now,
    bool filterTargets
) {
    for (auto it = devices.begin(); it != devices.end();) {
        const auto age =
            std::chrono::duration_cast<std::chrono::seconds>(
                now - it->second.lastSeen
            ).count();

        const bool stale = age > STALE_SECONDS;

        const bool unwanted =
            filterTargets &&
            !isTarget(it->second.name, it->first);

        if (stale || unwanted) {
            it = devices.erase(it);
        } else {
            ++it;
        }
    }
}

// ============================================================
// РЕЖИМ WORK
// ============================================================

// В этом режиме обрабатываются только целевые устройства.

void working_func(
    const std::vector<RawPacket>& batch,
    std::chrono::steady_clock::time_point now
) {
    seenThisWindow.clear();

    for (const auto& pkt : batch) {
        if (!isTarget(pkt.name, pkt.mac)) {
            continue;
        }

        updateDevice(pkt, now);

        seenThisWindow.insert(pkt.mac);
    }

    removeStaleDevices(now, true);
}

// ============================================================
// РЕЖИМ DETECT
// ============================================================

// В этом режиме обрабатываются все обнаруженные устройства.

void detecting_func(
    const std::vector<RawPacket>& batch,
    std::chrono::steady_clock::time_point now
) {
    seenThisWindow.clear();

    for (const auto& pkt : batch) {
        updateDevice(pkt, now);

        seenThisWindow.insert(pkt.mac);
    }

    removeStaleDevices(now, false);
}

// ============================================================
// ВЫВОД РЕЗУЛЬТАТОВ
// ============================================================

void printReport(
    int windowNo,
    size_t batchSize,
    size_t maxBatchSize
) {
    const Mode currentMode = g_mode.load();

    const char* modeStr =
        currentMode == Mode::Detect ? "DETECT" :
        currentMode == Mode::Work   ? "WORK"   :
                                      "PAUSE";

    std::cout
        << "=== Window " << windowNo
        << " [" << modeStr << "]"
        << " | events: " << batchSize
        << " | devices seen: " << seenThisWindow.size()
        << " | tracked: " << devices.size()
        << " | max batch: " << maxBatchSize
        << " | total callbacks: " << g_receivedEvents.load()
        << " ===\n";

    if (currentMode == Mode::Pause) {
        std::cout
            << "  (paused: scanning and event collection continue)\n\n";
        return;
    }

    if (seenThisWindow.empty()) {
        std::cout << "  (no devices in this window)\n\n";
        return;
    }

    for (const auto& mac : seenThisWindow) {
        const auto it = devices.find(mac);

        if (it == devices.end()) {
            continue;
        }

        const auto& info = it->second;

        if (info.rssiHistory.empty()) {
            continue;
        }

        double sum = 0.0;

        for (int value : info.rssiHistory) {
            sum += value;
        }

        const double avg =
            sum / static_cast<double>(info.rssiHistory.size());

        const double dist = calculateDistance(avg);

        const std::string displayName =
            info.name.empty() ? "<unnamed>" : info.name;

        std::cout
            << "  "
            << std::left
            << std::setw(24)
            << displayName
            << " [" << mac << "]"
            << " RSSI: "
            << std::setw(5)
            << info.rssiHistory.back()
            << " | avg: "
            << std::fixed
            << std::setprecision(1)
            << std::setw(6)
            << avg
            << " | ~"
            << std::setprecision(2)
            << dist
            << " m\n";
    }

    std::cout << "\n";
}

// ============================================================
// MAIN
// ============================================================

int main() {
    SetConsoleOutputCP(CP_UTF8);
    SetConsoleCP(CP_UTF8);

    // --------------------------------------------------------
    // 1. Получаем Bluetooth-адаптер
    // --------------------------------------------------------

    auto adapters = SimpleBLE::Adapter::get_adapters();

    if (adapters.empty()) {
        std::cerr << "No Bluetooth adapter found.\n";
        return 1;
    }

    auto adapter = adapters[0];

    std::cout
        << "Adapter: "
        << adapter.identifier()
        << " ["
        << adapter.address()
        << "]\n\n";

    std::cout
        << "Commands:\n"
        << "  q - quit\n"
        << "  p - pause output/tracking\n"
        << "  d - detect all devices\n"
        << "  w - work mode (target filter)\n"
        << "  b - begin CSV logging\n"
        << "  e - end CSV logging\n\n";

    // --------------------------------------------------------
    // 2. Регистрируем оба колбэка
    // --------------------------------------------------------

    adapter.set_callback_on_scan_found(
        [](SimpleBLE::Peripheral p) {
            enqueuePacket(std::move(p));
        }
    );

    adapter.set_callback_on_scan_updated(
        [](SimpleBLE::Peripheral p) {
            enqueuePacket(std::move(p));
        }
    );

    // --------------------------------------------------------
    // 3. Запускаем сканирование ОДИН раз
    // --------------------------------------------------------

    try {
        adapter.scan_start();
    } catch (const std::exception& e) {
        std::cerr
            << "Failed to start BLE scan: "
            << e.what()
            << "\n";

        return 1;
    }

    std::cout
        << "BLE scanning started.\n"
        << "Scanning remains active until exit.\n\n";

    // --------------------------------------------------------
    // 4. Основной цикл
    // --------------------------------------------------------

    int windowNo = 0;

    while (g_running.load()) {
        // Ожидаем окончания временного окна.
        // В это время BLE-колбэки продолжают заполнять очередь.

        const auto windowStart =
            std::chrono::steady_clock::now();

        while (g_running.load()) {
            if (_kbhit()) {
                handleKey(_getch());
            }

            if (!g_running.load()) {
                break;
            }

            const auto elapsed =
                std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::steady_clock::now() - windowStart
                ).count();

            if (elapsed >= TOTAL_WINDOW_MS) {
                break;
            }

            std::this_thread::sleep_for(
                std::chrono::milliseconds(POLL_STEP_MS)
            );
        }

        // ----------------------------------------------------
        // 5. Забираем накопленные события из очереди
        // ----------------------------------------------------

        std::vector<RawPacket> batch;

        {
            std::lock_guard<std::mutex> lock(g_queueMutex);

            batch.swap(g_rawPackets);
        }

        ++windowNo;

        const size_t batchSize = batch.size();

        if (batchSize > g_maxBatchSize) {
            g_maxBatchSize = batchSize;
        }

        // ----------------------------------------------------
        // 6. Записываем события в уже открытый CSV
        // ----------------------------------------------------

        // Сначала сохраняем накопившуюся партию, если логирование
        // уже было включено до начала этого окна.
        //
        // При нажатии e текущая партия ещё попадёт в файл.

        writeBatchToCsv(batch);

        // ----------------------------------------------------
        // 7. Обрабатываем команды включения/выключения логирования
        // ----------------------------------------------------

        if (g_logging.load() && !g_logFile.is_open()) {
            startLogging();
        } else if (!g_logging.load() && g_logFile.is_open()) {
            stopLogging();
        }

        if (!g_running.load()) {
            break;
        }

        // ----------------------------------------------------
        // 8. Обрабатываем данные согласно выбранному режиму
        // ----------------------------------------------------

        const auto now = std::chrono::steady_clock::now();

        switch (g_mode.load()) {
            case Mode::Detect:
                detecting_func(batch, now);
                break;

            case Mode::Work:
                working_func(batch, now);
                break;

            case Mode::Pause:
                // Очередь уже обработана и извлечена.
                // Сканирование продолжает работать.
                seenThisWindow.clear();
                break;
        }

        // ----------------------------------------------------
        // 9. Выводим статистику
        // ----------------------------------------------------

        printReport(
            windowNo,
            batchSize,
            g_maxBatchSize
        );
    }

    // --------------------------------------------------------
    // 10. Завершение
    // --------------------------------------------------------

    try {
        adapter.scan_stop();
    } catch (const std::exception& e) {
        std::cerr
            << "Warning: failed to stop BLE scan: "
            << e.what()
            << "\n";
    }

    // Сохраняем события, оставшиеся в очереди перед остановкой.
    std::vector<RawPacket> finalBatch;

    {
        std::lock_guard<std::mutex> lock(g_queueMutex);

        finalBatch.swap(g_rawPackets);
    }

    writeBatchToCsv(finalBatch);

    if (g_logFile.is_open()) {
        stopLogging();
    }

    std::cout
        << "\nTotal callback events: "
        << g_receivedEvents.load()
        << "\n";

    std::cout << "Stopped.\n";

    return 0;
}
