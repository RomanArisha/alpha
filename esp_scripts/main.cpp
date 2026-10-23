#include <simpleble/SimpleBLE.h>
#include <iostream>
#include <iomanip>
#include <cmath>
#include <deque>
#include <map>
#include <set>
#include <string>
#include <chrono>
#include <atomic>
#include <mutex>
#include <vector>
#include <array>
#include <string_view>
#include <thread>
#include <cctype>
#include <utility>

#ifdef _WIN32
    #include <conio.h>
    #include <windows.h>
#else
    #include <termios.h>
    #include <unistd.h>
    #include <fcntl.h>
    #include <sys/ioctl.h>
#endif

#ifdef _WIN32
    // На Windows используем стандартные _kbhit и _getch
    bool kbhit() { return _kbhit() != 0; }
    int getch() { return _getch(); }
#else
    // На Linux эмулируем поведение через termios
    bool kbhit() {
        termios term;
        tcgetattr(0, &term);
        termios term2 = term;
        term2.c_lflag &= ~ICANON;
        term2.c_lflag &= ~ECHO;
        term2.c_cc[VMIN] = 0;
        term2.c_cc[VTIME] = 0;
        tcsetattr(0, TCSANOW, &term2);

        int byteswaiting;
        ioctl(0, FIONREAD, &byteswaiting);

        tcsetattr(0, TCSANOW, &term);
        return byteswaiting > 0;
    }

    int getch() {
        termios term;
        tcgetattr(0, &term);
        termios term2 = term;
        term2.c_lflag &= ~ICANON;
        term2.c_lflag &= ~ECHO;
        term2.c_cc[VMIN] = 1;
        term2.c_cc[VTIME] = 0;
        tcsetattr(0, TCSANOW, &term2);

        int ch = getchar();

        tcsetattr(0, TCSANOW, &term);
        return ch;
    }
#endif

struct DeviceInfo {
    std::string name;
    std::deque<int> rssiHistory;
    std::chrono::steady_clock::time_point lastSeen;
};

struct RawPacket {
    std::string mac; //мак-адрес (по нему в перспективе должны идентифицировать наши метки)
    std::string name; //имя в системе - по нему нужно идентифицировать смартфоны
    int rssi; 
};

// --- Калибровка точности и времени работы ---
constexpr double RSSI_AT_1M = -66.0; //уровень RSSI на 1м, самый важный параметр для точности
constexpr double PATH_LOSS_EXPONENT = 2.5; //параметр для рассчитывания расстояния
constexpr size_t HISTORY_SIZE = 1; //количество последних нахождений объектов, которые будут использоваться для сглаживания расстояния
constexpr int    STALE_SECONDS = 60; //через 60 секунд отсутсвия объект признаётся неактивным и удаляется из хранилища
constexpr int    TOTAL_WINDOW_MS = 5000; //период обноваления информации и вывода на экран (мс)
constexpr int    POLL_STEP_MS    = 100; //время для считывания инструкций с клавиатуры

constexpr std::array<std::string_view, 1> TARGETS = { //список имён целей
    //при работе с esp и метками лучше использовать mac-адреса
    "POCO X7 Pro", //имя моего телефончика
};

bool iequals(std::string_view a, std::string_view b) { //функция для сравнения двух строк (чтобы сравнивать имена устройств)
    if (a.size() != b.size()) return false;
    for (size_t i = 0; i < a.size(); ++i)
        if (std::tolower(static_cast<unsigned char>(a[i])) !=
            std::tolower(static_cast<unsigned char>(b[i])))
            return false;
    return true;
}

bool isTarget(const std::string& name, const std::string& mac) { //функция для проверки, является ли найденный объект целевым
    for (auto t : TARGETS)
        if (iequals(name, t) || iequals(mac, t)) return true;
    return false;
}

// Один «сырой» пакет: то, что колбэк складывает в очередь

std::mutex g_queueMutex; //позволяет безопасно работать с многопоточностью, многопоточность - для считывания с клавиатры команд и считывания сканером ble-устройств
std::vector<RawPacket> g_rawPackets;
std::map<std::string, DeviceInfo> devices;
std::set<std::string> seenThisWindow;

std::atomic<bool> g_running{true};//переменная для отслеживания завершения программы

enum class Mode { 
    Work, Detect, Pause //три возможных режима работы
};
std::atomic<Mode> g_mode{Mode::Work}; //атомарная переменная для хранения режима работы программы

void handleKey(int key) { //обработка ввода команд
    switch (key) {
        case 'p': g_mode = Mode::Pause;  break;
        case 'd': g_mode = Mode::Detect; break;
        case 'w': g_mode = Mode::Work;   break;
        case 'q': g_running = false;     break;
    }
}

double calculateDistance(double rssi) { //функция расчёта расстояния по значению rssi и указанным параметрам
    if (rssi == 0.0) return -1.0;
    return std::pow(10.0, (RSSI_AT_1M - rssi) / (10.0 * PATH_LOSS_EXPONENT));
}

void working_func(const std::vector<RawPacket>& batch, //функция для работы в режиме обнаружения заданных целей и вычисления расстояния до них
                  std::chrono::steady_clock::time_point now){
    seenThisWindow.clear();
    for (auto& pkt : batch) {
        if (!isTarget(pkt.name, pkt.mac))
            continue;
        auto& info = devices[pkt.mac];
        if (info.name.empty() && !pkt.name.empty()) info.name = pkt.name;
        info.rssiHistory.push_back(pkt.rssi);
        if (info.rssiHistory.size() > HISTORY_SIZE) info.rssiHistory.pop_front();
        info.lastSeen = now;
        seenThisWindow.insert(pkt.mac);
    }

    // 4) Удаляем устройства, молчащие дольше STALE_SECONDS
    for (auto it = devices.begin(); it != devices.end(); ) {
        auto age = std::chrono::duration_cast<std::chrono::seconds>(
                        now - it->second.lastSeen).count();
        if (age > STALE_SECONDS || !isTarget(it->second.name, it->first)) it = devices.erase(it);
        else ++it;
    }
}

void detecting_func(const std::vector<RawPacket>& batch, //функция для обнаружения и вывода всех устройств, которые видит сканер (ноут)
                  std::chrono::steady_clock::time_point now){
    seenThisWindow.clear();
    for (auto& pkt : batch) {
        auto& info = devices[pkt.mac];
        if (info.name.empty() && !pkt.name.empty()) info.name = pkt.name;
        info.rssiHistory.push_back(pkt.rssi);
        if (info.rssiHistory.size() > HISTORY_SIZE) info.rssiHistory.pop_front();
        info.lastSeen = now;
        seenThisWindow.insert(pkt.mac);
    }

    // 4) Удаляем устройства, молчащие дольше STALE_SECONDS
    for (auto it = devices.begin(); it != devices.end(); ) {
        auto age = std::chrono::duration_cast<std::chrono::seconds>(
                        now - it->second.lastSeen).count();
        if (age > STALE_SECONDS) it = devices.erase(it);
        else ++it;
    }
}

int main() {
    #ifdef _WIN32
        SetConsoleOutputCP(65001); // только для Windows
    #endif

    auto adapters = SimpleBLE::Adapter::get_adapters();
    if (adapters.empty()) { std::cerr << "No Bluetooth adapter found.\n"; return 1; }
    auto adapter = adapters[0]; //самый первый попавшийся найденный адаптер на нуотбуке - сам ноутбук, поэтому берём его для работы
    std::cout << "Adapter: " << adapter.identifier()
              << " [" << adapter.address() << "]\n"
              << "Press q to stop.\n"
              << "Press p to pause.\n"
              << "Press d to detecting devices.\n"
              << "Press w to working mode (filter to targets).\n\n";

    // --- Колбэк: только сбор данных, никакой логики ---
    adapter.set_callback_on_scan_found([&](SimpleBLE::Peripheral p) {
        RawPacket pkt;
        pkt.mac  = p.address();     // BluetoothAddress → std::string
        pkt.name = p.identifier();  // GAP-имя устройства (может быть пустым)
        pkt.rssi = p.rssi();

        std::lock_guard<std::mutex> lock(g_queueMutex);
        g_rawPackets.push_back(std::move(pkt));
    });

    int windowNo = 0;
    while (g_running) {

        // 1) сканируем
        adapter.scan_start();
        int elapsed = 0;
        while (elapsed < TOTAL_WINDOW_MS && g_running) {
            if (kbhit()) handleKey(getch());
            std::this_thread::sleep_for(std::chrono::milliseconds(POLL_STEP_MS));
            elapsed += POLL_STEP_MS;
        }
        adapter.scan_stop();

        if (!g_running) break;
        if (g_mode.load() == Mode::Pause) { //в случае паузы очищаем и переходим в режим ожидания
            std::lock_guard<std::mutex> lock(g_queueMutex);
            g_rawPackets.clear();
            continue;
        }

        // 2) Забираем все накопленные пакеты O(1) через swap
        std::vector<RawPacket> batch;
        {
            std::lock_guard<std::mutex> lock(g_queueMutex);
            batch.swap(g_rawPackets);   // g_rawPackets становится пустым
        }

        ++windowNo;
        auto now = std::chrono::steady_clock::now();

        switch (g_mode.load()) {
            case Mode::Detect: detecting_func(batch, now); break;
            case Mode::Work:   working_func(batch, now);   break;
            case Mode::Pause: break;
        }

        // 5) Печатаем отчёт
        const char* modeStr = (g_mode.load() == Mode::Detect) ? "DETECT" : "WORK";
        std::cout << "=== Window " << windowNo
                << " [" << modeStr << "]"
                << " | devices seen: " << seenThisWindow.size()
                << " | tracked: " << devices.size() << " ===\n";

        if (seenThisWindow.empty()) {
            std::cout << "  (no devices)\n\n";
            continue;
        }

        for (const std::string& mac : seenThisWindow) {
            auto it = devices.find(mac);
            if (it == devices.end()) continue;
            auto& info = it->second;
            if (info.rssiHistory.empty()) continue;

            double sum = 0;
            for (int v : info.rssiHistory) sum += v;
            double avg = sum / info.rssiHistory.size();
            double dist = calculateDistance(avg);
            std::string displayName = info.name.empty() ? "<unnamed>" : info.name;

            std::cout << "  " << std::left << std::setw(24) << displayName
                    << " [" << mac << "]"
                    << " RSSI: " << std::setw(5) << info.rssiHistory.back()
                    << " | avg: " << std::fixed << std::setprecision(1) << std::setw(6) << avg
                    << " | ~" << std::setprecision(2) << dist << " m\n";
        }
        std::cout << "\n";
    }

    std::cout << "Stopped.\n";
    return 0;
}