// SubHub.exe — надёжный лаунчер GUI рядом с app.py
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

internal static class Program
{
    const string MutexName = "Global\\Crownfall.SubHub.GUI.v1";
    const string ActivateEventName = "Local\\Crownfall.SubHub.Activate.v1";
    const int ERROR_FILE_NOT_FOUND = 2;
    const int SW_RESTORE = 9;
    const int SW_SHOW = 5;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr OpenMutex(uint dwDesiredAccess, bool bInheritHandle, string lpName);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateEvent(IntPtr lpEventAttributes, bool bManualReset, bool bInitialState, string lpName);

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetEvent(IntPtr hEvent);

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool CloseHandle(IntPtr hObject);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr FindWindow(string lpClassName, string lpWindowName);

    [DllImport("user32.dll")]
    static extern bool IsIconic(IntPtr hWnd);

    [DllImport("user32.dll")]
    static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    static extern bool BringWindowToTop(IntPtr hWnd);

    [STAThread]
    static int Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(
            Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        Directory.SetCurrentDirectory(root);

        try
        {
            string appPy = Path.Combine(root, "app.py");
            if (!File.Exists(appPy))
            {
                Fail("Не найден app.py рядом с SubHub.exe:\n" + root, root);
                return 1;
            }

            // Уже запущено — поднять окно, не плодить второй pythonw
            if (IsSubHubRunning())
            {
                RequestActivate(root);
                ActivateSubHubWindow();
                return 0;
            }

            string pythonw = FindPythonw();
            if (string.IsNullOrEmpty(pythonw))
            {
                Fail(
                    "Не найден pythonw.exe.\n" +
                    "Установите Python и отметьте «Add python.exe to PATH»,\n" +
                    "или запустите: app.bat --console",
                    root);
                return 1;
            }

            // Обёртка: любой краш пишется в data/pythonw_crash.log
            string boot = Path.Combine(root, "scripts", "_gui_boot.py");
            if (!File.Exists(boot))
                boot = appPy;

            var psi = new ProcessStartInfo
            {
                FileName = pythonw,
                Arguments = "\"" + boot + "\"",
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            psi.EnvironmentVariables["PYTHONUTF8"] = "1";
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            psi.EnvironmentVariables["SUBHUB_LAUNCHED_BY"] = "SubHub.exe";

            Process child;
            try
            {
                child = Process.Start(psi);
            }
            catch (Exception ex)
            {
                Fail("Не удалось запустить Python:\n" + ex.Message, root);
                return 1;
            }

            if (child == null)
            {
                Fail("Process.Start вернул null.", root);
                return 1;
            }

            // Быстрый контроль: если процесс умер за 1.2s — показать ошибку
            for (int i = 0; i < 12; i++)
            {
                if (child.HasExited)
                {
                    string hint = ReadTail(Path.Combine(root, "data", "pythonw_crash.log"), 1200);
                    if (string.IsNullOrEmpty(hint))
                        hint = ReadTail(Path.Combine(root, "data", "app_crash.log"), 1200);
                    Fail(
                        "SubHub сразу завершился (код " + child.ExitCode + ").\n\n" +
                        (string.IsNullOrEmpty(hint) ? "Смотрите data\\pythonw_crash.log" : hint),
                        root);
                    return 1;
                }
                // Уже есть mutex / окно — ок
                if (IsSubHubRunning() || FindWindow(null, "SubHub") != IntPtr.Zero)
                    return 0;
                Thread.Sleep(100);
            }
            return 0;
        }
        catch (Exception ex)
        {
            Fail(ex.ToString(), root);
            return 1;
        }
    }

    static bool IsSubHubRunning()
    {
        const uint SYNCHRONIZE = 0x00100000;
        IntPtr h = OpenMutex(SYNCHRONIZE, false, MutexName);
        if (h == IntPtr.Zero)
            return false;
        CloseHandle(h);
        return true;
    }

    static void RequestActivate(string root)
    {
        try
        {
            IntPtr ev = CreateEvent(IntPtr.Zero, false, false, ActivateEventName);
            if (ev != IntPtr.Zero)
            {
                SetEvent(ev);
                CloseHandle(ev);
            }
        }
        catch { /* ignore */ }

        try
        {
            string data = Path.Combine(root, "data");
            Directory.CreateDirectory(data);
            File.WriteAllText(
                Path.Combine(data, "activate.request"),
                DateTime.UtcNow.Ticks.ToString(),
                Encoding.UTF8);
        }
        catch { /* ignore */ }
    }

    static void ActivateSubHubWindow()
    {
        IntPtr hwnd = FindWindow(null, "SubHub");
        if (hwnd == IntPtr.Zero)
            hwnd = FindWindow("TkTopLevel", "SubHub");
        if (hwnd == IntPtr.Zero)
            return;
        if (IsIconic(hwnd))
            ShowWindow(hwnd, SW_RESTORE);
        else
            ShowWindow(hwnd, SW_SHOW);
        BringWindowToTop(hwnd);
        SetForegroundWindow(hwnd);
    }

    static string FindPythonw()
    {
        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string[] candidates = {
            Path.Combine(local, "Python", "bin", "pythonw.exe"),
            Path.Combine(local, "Programs", "Python", "Python314", "pythonw.exe"),
            Path.Combine(local, "Programs", "Python", "Python313", "pythonw.exe"),
            Path.Combine(local, "Programs", "Python", "Python312", "pythonw.exe"),
            Path.Combine(local, "Programs", "Python", "Python311", "pythonw.exe"),
            @"C:\Python314\pythonw.exe",
            @"C:\Python313\pythonw.exe",
            @"C:\Python312\pythonw.exe",
        };
        foreach (string p in candidates)
        {
            if (File.Exists(p)) return p;
        }

        try
        {
            // Без cmd/where — только известные пути (нет вспышки консоли)
            return null;
        }
        catch { /* ignore */ }
        return null;
    }

    static void Fail(string message, string root)
    {
        try
        {
            Directory.CreateDirectory(Path.Combine(root, "data"));
            File.WriteAllText(
                Path.Combine(root, "data", "launch_error.log"),
                DateTime.Now + "\r\n" + message + "\r\n",
                Encoding.UTF8);
        }
        catch { /* ignore */ }
        MessageBox.Show(message, "SubHub", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }

    static string ReadTail(string path, int maxChars)
    {
        try
        {
            if (!File.Exists(path)) return "";
            string s = File.ReadAllText(path, Encoding.UTF8);
            if (s.Length <= maxChars) return s;
            return s.Substring(s.Length - maxChars);
        }
        catch { return ""; }
    }
}
