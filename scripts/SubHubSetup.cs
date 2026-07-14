// SubHub-Setup.exe — установщик и деинсталлятор (без Inno Setup).
// Встраивает payload.zip (/resource), ставит в %LocalAppData%\Programs\SubHub,
// ярлык на рабочий стол, запись в «Приложения», без автозапуска по умолчанию.
// Копия с именем «Uninstall SubHub.exe» запускается сразу в режиме удаления.
using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.IO.Compression;
using System.Management;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;
using Microsoft.Win32;

internal static class Program
{
    internal const string AppName = "SubHub";
    internal const string Publisher = "Crownfall";
    internal const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\CrownfallSubHub";

    [STAThread]
    static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);

        bool uninstall = false, quiet = false;
        foreach (var a in args)
        {
            if (Eq(a, "/uninstall") || Eq(a, "--uninstall")) uninstall = true;
            if (Eq(a, "/quiet") || Eq(a, "/S") || Eq(a, "--quiet")) quiet = true;
        }

        // «Uninstall SubHub.exe» — копия установщика: без аргументов тоже должна удалять
        string selfName = Path.GetFileNameWithoutExtension(Application.ExecutablePath);
        if (selfName.IndexOf("uninstall", StringComparison.OrdinalIgnoreCase) >= 0 ||
            selfName.IndexOf("удал", StringComparison.OrdinalIgnoreCase) >= 0)
            uninstall = true;

        string version = ReadEmbeddedVersion() ?? "1.4.1";

        if (uninstall)
        {
            if (quiet)
            {
                string dir = ResolveInstallDir();
                int rc = UninstallCore(dir, false);
                ScheduleSelfDelete(dir, false);
                return rc;
            }
            Application.Run(new UninstallForm(version));
            return 0;
        }

        Application.Run(new SetupForm(version));
        return 0;
    }

    static bool Eq(string a, string b)
    {
        return string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
    }

    static string ReadEmbeddedVersion()
    {
        try
        {
            using (var s = Assembly.GetExecutingAssembly().GetManifestResourceStream("VERSION"))
            {
                if (s == null) return null;
                using (var r = new StreamReader(s))
                    return (r.ReadLine() ?? "").Trim();
            }
        }
        catch { return null; }
    }

    internal static string DefaultInstallDir()
    {
        return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Programs", AppName);
    }

    // Папка установки: рядом с деинсталлятором → из реестра → по умолчанию.
    // Только по маркеру .installed; папка с .git (репозиторий разработки) — никогда.
    internal static string ResolveInstallDir()
    {
        try
        {
            string self = Path.GetDirectoryName(Application.ExecutablePath);
            if (!string.IsNullOrEmpty(self) &&
                File.Exists(Path.Combine(self, ".installed")) &&
                !Directory.Exists(Path.Combine(self, ".git")))
                return self;
        }
        catch { }
        try
        {
            using (RegistryKey key = Registry.CurrentUser.OpenSubKey(UninstallKey))
            {
                if (key != null)
                {
                    object loc = key.GetValue("InstallLocation");
                    if (loc != null && Directory.Exists(loc.ToString()))
                        return loc.ToString();
                }
            }
        }
        catch { }
        return DefaultInstallDir();
    }

    internal static void ExtractPayload(string destDir, Action<int> progress)
    {
        Directory.CreateDirectory(destDir);
        using (var s = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
        {
            if (s == null)
                throw new InvalidOperationException("В установщике нет payload.zip (собери через scripts\\build_installer.bat)");
            string tmpZip = Path.Combine(Path.GetTempPath(), "SubHubPayload_" + Guid.NewGuid().ToString("N") + ".zip");
            string tmpOut = Path.Combine(Path.GetTempPath(), "SubHubExtract_" + Guid.NewGuid().ToString("N"));
            try
            {
                using (var fs = File.Create(tmpZip))
                    s.CopyTo(fs);
                Directory.CreateDirectory(tmpOut);
                ZipFile.ExtractToDirectory(tmpZip, tmpOut);
                CopyTree(tmpOut, destDir, progress);
            }
            finally
            {
                try { File.Delete(tmpZip); } catch { }
                try { Directory.Delete(tmpOut, true); } catch { }
            }
        }
    }

    static void CopyTree(string src, string dst, Action<int> progress)
    {
        Directory.CreateDirectory(dst);
        foreach (string dir in Directory.GetDirectories(src, "*", SearchOption.AllDirectories))
        {
            string rel = dir.Substring(src.Length).TrimStart('\\', '/');
            Directory.CreateDirectory(Path.Combine(dst, rel));
        }
        string[] files = Directory.GetFiles(src, "*", SearchOption.AllDirectories);
        for (int i = 0; i < files.Length; i++)
        {
            string rel = files[i].Substring(src.Length).TrimStart('\\', '/');
            string target = Path.Combine(dst, rel);
            Directory.CreateDirectory(Path.GetDirectoryName(target));
            File.Copy(files[i], target, true);
            if (progress != null && files.Length > 0)
                progress((i + 1) * 100 / files.Length);
        }
    }

    internal static void CreateShortcut(string lnkPath, string target, string workDir, string iconPath)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(lnkPath));
        // WScript.Shell via COM
        Type t = Type.GetTypeFromProgID("WScript.Shell");
        object shell = Activator.CreateInstance(t);
        object shortcut = t.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { lnkPath });
        Type st = shortcut.GetType();
        st.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut, new object[] { target });
        st.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, shortcut, new object[] { workDir });
        st.InvokeMember("Description", BindingFlags.SetProperty, null, shortcut, new object[] { AppName });
        st.InvokeMember("IconLocation", BindingFlags.SetProperty, null, shortcut, new object[] { iconPath + ",0" });
        st.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, null);
    }

    internal static void RegisterUninstall(string installDir, string version, string setupExePath)
    {
        string uninstallCmd = "\"" + setupExePath + "\" /uninstall";
        string localSetup = Path.Combine(installDir, "Uninstall SubHub.exe");
        try
        {
            File.Copy(setupExePath, localSetup, true);
            uninstallCmd = "\"" + localSetup + "\" /uninstall";
        }
        catch { }

        long sizeKb = 180000;
        try
        {
            long bytes = 0;
            foreach (string f in Directory.GetFiles(installDir, "*", SearchOption.AllDirectories))
                bytes += new FileInfo(f).Length;
            sizeKb = bytes / 1024;
        }
        catch { }

        string exe = Path.Combine(installDir, "SubHub.exe");
        using (RegistryKey key = Registry.CurrentUser.CreateSubKey(UninstallKey))
        {
            key.SetValue("DisplayName", AppName);
            key.SetValue("DisplayVersion", version);
            key.SetValue("Publisher", Publisher);
            key.SetValue("InstallLocation", installDir);
            key.SetValue("DisplayIcon", exe + ",0");
            key.SetValue("UninstallString", uninstallCmd);
            key.SetValue("QuietUninstallString", uninstallCmd + " /quiet");
            key.SetValue("NoModify", 1, RegistryValueKind.DWord);
            key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            key.SetValue("EstimatedSize", (int)Math.Min(sizeKb, int.MaxValue), RegistryValueKind.DWord);
        }
    }

    // Закрыть SubHub.exe и его pythonw/python, запущенные из папки установки
    internal static void KillApp(string installDir)
    {
        try
        {
            foreach (var p in Process.GetProcessesByName("SubHub"))
            {
                try { p.Kill(); p.WaitForExit(3000); } catch { }
            }
        }
        catch { }

        try
        {
            string needle = installDir.TrimEnd('\\').ToLowerInvariant();
            using (var searcher = new ManagementObjectSearcher(
                "SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='pythonw.exe' OR Name='python.exe'"))
            {
                foreach (ManagementObject mo in searcher.Get())
                {
                    string cmd = Convert.ToString(mo["CommandLine"]);
                    if (string.IsNullOrEmpty(cmd) || cmd.ToLowerInvariant().IndexOf(needle) < 0)
                        continue;
                    try
                    {
                        int pid = Convert.ToInt32(mo["ProcessId"]);
                        var p = Process.GetProcessById(pid);
                        p.Kill();
                        p.WaitForExit(3000);
                    }
                    catch { }
                }
            }
        }
        catch { }
    }

    // Удаление: ярлыки, реестр, файлы. removeData=true — стереть и данные/профили.
    internal static int UninstallCore(string installDir, bool removeData)
    {
        KillApp(installDir);

        string desk = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), AppName + ".lnk");
        string start = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            @"Microsoft\Windows\Start Menu\Programs", AppName + ".lnk");
        string startup = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.Startup), AppName + ".lnk");
        foreach (var f in new[] { desk, start, startup })
        {
            try { if (File.Exists(f)) File.Delete(f); } catch { }
        }

        try { Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false); } catch { }

        try
        {
            // Папку с .git (репозиторий разработки) не трогаем никогда
            if (Directory.Exists(installDir) &&
                !Directory.Exists(Path.Combine(installDir, ".git")))
            {
                string self = Application.ExecutablePath;
                foreach (var f in Directory.GetFiles(installDir, "*", SearchOption.TopDirectoryOnly))
                {
                    if (string.Equals(f, self, StringComparison.OrdinalIgnoreCase))
                        continue; // сам деинсталлятор сотрёт ScheduleSelfDelete
                    string name = Path.GetFileName(f);
                    if (!removeData && name.Equals("secrets.yaml", StringComparison.OrdinalIgnoreCase))
                        continue;
                    try { File.Delete(f); } catch { }
                }
                foreach (var d in Directory.GetDirectories(installDir))
                {
                    string name = Path.GetFileName(d);
                    if (!removeData)
                    {
                        if (name.Equals("data", StringComparison.OrdinalIgnoreCase)) continue;
                        if (name.Equals("chrome_profiles", StringComparison.OrdinalIgnoreCase)) continue;
                        if (name.Equals("chrome_profiles_done", StringComparison.OrdinalIgnoreCase)) continue;
                    }
                    try { Directory.Delete(d, true); } catch { }
                }
            }
        }
        catch { }

        return 0;
    }

    // Через 2 секунды после выхода: удалить сам деинсталлятор и папку (пустую или целиком)
    internal static void ScheduleSelfDelete(string installDir, bool removeAll)
    {
        string self = Application.ExecutablePath;
        string dirNorm = installDir.TrimEnd('\\', '/');
        bool selfInside = self.StartsWith(dirNorm + "\\", StringComparison.OrdinalIgnoreCase);
        if (!selfInside || Directory.Exists(Path.Combine(dirNorm, ".git")))
            return; // не из папки установки (dist/репозиторий) — ничего не трогаем

        string args = "/c ping 127.0.0.1 -n 3 >nul & del /f /q \"" + self + "\" & " +
            (removeAll ? "rd /s /q \"" + dirNorm + "\"" : "rd \"" + dirNorm + "\" 2>nul");
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = "cmd.exe",
                Arguments = args,
                WindowStyle = ProcessWindowStyle.Hidden,
                CreateNoWindow = true,
                UseShellExecute = false,
            });
        }
        catch { }
    }
}

// ── Общий стиль ──────────────────────────────────────────────────────────────
internal static class Ui
{
    public static readonly Color Bg = Color.FromArgb(11, 18, 32);
    public static readonly Color Field = Color.FromArgb(30, 41, 59);
    public static readonly Color Line = Color.FromArgb(51, 65, 85);
    public static readonly Color TextMain = Color.FromArgb(241, 245, 249);
    public static readonly Color TextDim = Color.FromArgb(148, 163, 184);
    public static readonly Color Accent = Color.FromArgb(34, 197, 94);
    public static readonly Color AccentHover = Color.FromArgb(74, 222, 128);
    public static readonly Color Teal = Color.FromArgb(45, 212, 191);
    public static readonly Color Danger = Color.FromArgb(239, 68, 68);
    public static readonly Color DangerHover = Color.FromArgb(248, 113, 113);

    [DllImport("gdi32.dll")]
    static extern IntPtr CreateRoundRectRgn(int l, int t, int r, int b, int w, int h);

    public static void Round(Control c, int radius)
    {
        c.Region = Region.FromHrgn(CreateRoundRectRgn(0, 0, c.Width + 1, c.Height + 1, radius, radius));
    }

    public static Button MakeButton(string text, Color back, Color hover, Color fore, int w, int h)
    {
        var b = new Button
        {
            Text = text,
            Width = w,
            Height = h,
            FlatStyle = FlatStyle.Flat,
            BackColor = back,
            ForeColor = fore,
            Font = new Font("Segoe UI", 10f, FontStyle.Bold),
            Cursor = Cursors.Hand,
            TabStop = true,
        };
        b.FlatAppearance.BorderSize = 0;
        b.FlatAppearance.MouseOverBackColor = hover;
        b.FlatAppearance.MouseDownBackColor = back;
        b.HandleCreated += delegate { Round(b, 10); };
        return b;
    }

    public static TextBox MakeTextBox(int left, int top, int width)
    {
        return new TextBox
        {
            Left = left, Top = top, Width = width,
            BackColor = Field,
            ForeColor = TextMain,
            BorderStyle = BorderStyle.FixedSingle,
            Font = new Font("Segoe UI", 10f),
        };
    }

    public static CheckBox MakeCheck(string text, bool state, int left, int top)
    {
        return new CheckBox
        {
            Text = text,
            Checked = state,
            Left = left, Top = top,
            AutoSize = true,
            ForeColor = TextMain,
            Cursor = Cursors.Hand,
        };
    }
}

// Тонкий индикатор прогресса
internal sealed class SlimProgress : Control
{
    int _value;
    public int Value
    {
        get { return _value; }
        set { _value = Math.Max(0, Math.Min(100, value)); Invalidate(); }
    }

    public SlimProgress()
    {
        SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint |
                 ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        Height = 8;
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        using (var back = new SolidBrush(Ui.Field))
            FillRounded(g, back, new Rectangle(0, 0, Width, Height), Height / 2);
        int w = (int)(Width * (_value / 100.0));
        if (w > Height)
        {
            var rect = new Rectangle(0, 0, w, Height);
            using (var fill = new LinearGradientBrush(rect, Ui.Accent, Ui.Teal, LinearGradientMode.Horizontal))
                FillRounded(g, fill, rect, Height / 2);
        }
    }

    static void FillRounded(Graphics g, Brush b, Rectangle r, int rad)
    {
        using (var path = new GraphicsPath())
        {
            int d = rad * 2;
            path.AddArc(r.X, r.Y, d, d, 180, 90);
            path.AddArc(r.Right - d - 1, r.Y, d, d, 270, 90);
            path.AddArc(r.Right - d - 1, r.Bottom - d - 1, d, d, 0, 90);
            path.AddArc(r.X, r.Bottom - d - 1, d, d, 90, 90);
            path.CloseFigure();
            g.FillPath(b, path);
        }
    }
}

// Тёмная безрамочная форма: шапка с логотипом, версией и крестиком, перетаскивание
internal class DarkForm : Form
{
    [DllImport("user32.dll")] static extern bool ReleaseCapture();
    [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr hWnd, int msg, int wParam, int lParam);
    const int WM_NCLBUTTONDOWN = 0xA1;
    const int HT_CAPTION = 0x2;

    protected readonly Panel Header;

    public DarkForm(string title, string version, int width, int height, Color logoA, Color logoB)
    {
        FormBorderStyle = FormBorderStyle.None;
        Width = width;
        Height = height;
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Ui.Bg;
        ForeColor = Ui.TextMain;
        Font = new Font("Segoe UI", 10f);
        Text = title;
        try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
        Ui.Round(this, 18);

        Header = new Panel { Left = 0, Top = 0, Width = width, Height = 64, BackColor = Ui.Bg };
        Header.MouseDown += DragMove;

        var logo = new Panel { Left = 24, Top = 16, Width = 36, Height = 36 };
        logo.Paint += delegate(object s, PaintEventArgs e)
        {
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            var r = new Rectangle(0, 0, 35, 35);
            using (var path = RoundedPath(r, 10))
            using (var br = new LinearGradientBrush(r, logoA, logoB, 45f))
                e.Graphics.FillPath(br, path);
            using (var f = new Font("Segoe UI", 15f, FontStyle.Bold))
            using (var white = new SolidBrush(Color.White))
            {
                var sz = e.Graphics.MeasureString("S", f);
                e.Graphics.DrawString("S", f, white, (36 - sz.Width) / 2f, (36 - sz.Height) / 2f);
            }
        };
        logo.MouseDown += DragMove;

        var titleLbl = new Label
        {
            Text = title,
            Font = new Font("Segoe UI", 14f, FontStyle.Bold),
            ForeColor = Color.White,
            AutoSize = true,
            Left = 70, Top = 20,
            BackColor = Color.Transparent,
        };
        titleLbl.MouseDown += DragMove;

        var chip = new Label
        {
            Text = "v" + version,
            Font = new Font("Segoe UI", 8.5f),
            ForeColor = Ui.TextDim,
            BackColor = Ui.Field,
            AutoSize = false,
            TextAlign = ContentAlignment.MiddleCenter,
            Width = 52, Height = 20,
        };
        chip.HandleCreated += delegate { Ui.Round(chip, 10); };
        titleLbl.SizeChanged += delegate
        {
            chip.Left = titleLbl.Right + 8;
            chip.Top = titleLbl.Top + (titleLbl.Height - chip.Height) / 2;
        };
        chip.Left = titleLbl.Right + 8;
        chip.Top = 24;

        var close = new Label
        {
            Text = "✕",
            Font = new Font("Segoe UI", 11f),
            ForeColor = Ui.TextDim,
            AutoSize = false,
            TextAlign = ContentAlignment.MiddleCenter,
            Width = 36, Height = 30,
            Left = width - 48, Top = 14,
            Cursor = Cursors.Hand,
        };
        close.MouseEnter += delegate { close.ForeColor = Color.White; close.BackColor = Ui.Danger; };
        close.MouseLeave += delegate { close.ForeColor = Ui.TextDim; close.BackColor = Color.Transparent; };
        close.HandleCreated += delegate { Ui.Round(close, 8); };
        close.Click += delegate { Close(); };

        Header.Controls.Add(logo);
        Header.Controls.Add(titleLbl);
        Header.Controls.Add(chip);
        Header.Controls.Add(close);
        Controls.Add(Header);
    }

    void DragMove(object sender, MouseEventArgs e)
    {
        if (e.Button == MouseButtons.Left)
        {
            ReleaseCapture();
            SendMessage(Handle, WM_NCLBUTTONDOWN, HT_CAPTION, 0);
        }
    }

    protected static GraphicsPath RoundedPath(Rectangle r, int rad)
    {
        var path = new GraphicsPath();
        int d = rad * 2;
        path.AddArc(r.X, r.Y, d, d, 180, 90);
        path.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
        path.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
        path.CloseFigure();
        return path;
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        // Акцентная полоса сверху + рамка
        using (var strip = new LinearGradientBrush(
            new Rectangle(0, 0, Width, 3), Ui.Accent, Ui.Teal, LinearGradientMode.Horizontal))
            e.Graphics.FillRectangle(strip, 0, 0, Width, 3);
        using (var pen = new Pen(Ui.Line))
            e.Graphics.DrawRectangle(pen, 0, 0, Width - 1, Height - 1);
    }
}

// ── Установка ────────────────────────────────────────────────────────────────
internal sealed class SetupForm : DarkForm
{
    readonly string _version;
    readonly TextBox _dir;
    readonly Button _browse;
    readonly CheckBox _desktop;
    readonly CheckBox _startup;
    readonly CheckBox _launch;
    readonly SlimProgress _bar;
    readonly Label _status;
    readonly Button _install;
    readonly Button _cancel;
    bool _done;

    public SetupForm(string version)
        : base("SubHub", version, 560, 440, Ui.Accent, Ui.Teal)
    {
        _version = version;
        Text = "Установка SubHub " + version;

        var sub = new Label
        {
            Text = "Установка приложения · процессы работают только пока SubHub запущен",
            AutoSize = true,
            Left = 26, Top = 72,
            ForeColor = Ui.TextDim,
        };

        var dirLbl = new Label { Text = "Папка установки", Left = 24, Top = 112, AutoSize = true, ForeColor = Ui.TextDim };
        _dir = Ui.MakeTextBox(24, 136, 408);
        _dir.Text = Program.DefaultInstallDir();
        _browse = Ui.MakeButton("…", Ui.Field, Ui.Line, Color.White, 90, 28);
        _browse.Left = 444;
        _browse.Top = 134;
        _browse.Font = new Font("Segoe UI", 10f);
        _browse.Click += delegate
        {
            using (var d = new FolderBrowserDialog())
            {
                d.SelectedPath = _dir.Text;
                if (d.ShowDialog(this) == DialogResult.OK)
                    _dir.Text = d.SelectedPath;
            }
        };

        _desktop = Ui.MakeCheck("Ярлык на рабочем столе", true, 24, 182);
        _startup = Ui.MakeCheck("Автозапуск Windows (выкл. по умолчанию)", false, 24, 212);
        _launch = Ui.MakeCheck("Запустить SubHub после установки", true, 24, 242);

        _bar = new SlimProgress { Left = 24, Top = 292, Width = 510, Visible = false };

        _status = new Label
        {
            Text = "",
            Left = 24, Top = 310, Width = 510, Height = 40,
            ForeColor = Ui.TextDim,
        };

        _install = Ui.MakeButton("Установить", Ui.Accent, Ui.AccentHover, Color.FromArgb(2, 6, 23), 150, 38);
        _install.Left = 288;
        _install.Top = 372;
        _install.Click += delegate { OnInstallClick(); };

        _cancel = Ui.MakeButton("Отмена", Ui.Field, Ui.Line, Color.White, 86, 38);
        _cancel.Left = 448;
        _cancel.Top = 372;
        _cancel.Click += delegate { Close(); };

        Controls.Add(sub);
        Controls.Add(dirLbl);
        Controls.Add(_dir);
        Controls.Add(_browse);
        Controls.Add(_desktop);
        Controls.Add(_startup);
        Controls.Add(_launch);
        Controls.Add(_bar);
        Controls.Add(_status);
        Controls.Add(_install);
        Controls.Add(_cancel);
        AcceptButton = _install;
        CancelButton = _cancel;
    }

    void OnInstallClick()
    {
        if (_done) { Close(); return; }
        try
        {
            _install.Enabled = false;
            _browse.Enabled = false;
            _dir.Enabled = false;
            _bar.Visible = true;
            _bar.Value = 0;
            _status.ForeColor = Ui.TextDim;
            _status.Text = "Распаковка…";
            Application.DoEvents();

            string dest = _dir.Text.Trim();
            if (string.IsNullOrWhiteSpace(dest))
                throw new Exception("Укажите папку установки");

            // Не затирать secrets.yaml при обновлении
            string secretsBak = null;
            string secretsPath = Path.Combine(dest, "secrets.yaml");
            if (File.Exists(secretsPath))
            {
                secretsBak = Path.GetTempFileName();
                File.Copy(secretsPath, secretsBak, true);
            }

            Program.ExtractPayload(dest, delegate(int pct)
            {
                _bar.Value = pct;
                if (pct % 5 == 0) Application.DoEvents();
            });

            if (secretsBak != null && File.Exists(secretsBak))
            {
                File.Copy(secretsBak, secretsPath, true);
                try { File.Delete(secretsBak); } catch { }
            }

            // Настройки по умолчанию: без постоянного фона
            string dataDir = Path.Combine(dest, "data");
            Directory.CreateDirectory(dataDir);
            string settingsPath = Path.Combine(dataDir, "app_settings.json");
            if (!File.Exists(settingsPath))
            {
                File.WriteAllText(settingsPath,
                    "{\n  \"background_mode\": false,\n  \"minimize_to_tray\": false,\n" +
                    "  \"run_at_startup\": false,\n  \"start_minimized\": false,\n" +
                    "  \"notify_ggs_orders\": true,\n  \"notify_ggs_messages\": true\n}\n",
                    Encoding.UTF8);
            }

            string exe = Path.Combine(dest, "SubHub.exe");
            if (!File.Exists(exe))
                throw new Exception("После распаковки нет SubHub.exe");

            string ico = Path.Combine(dest, "assets", "app.ico");
            if (!File.Exists(ico)) ico = exe;

            _status.Text = "Ярлыки и регистрация…";
            Application.DoEvents();

            string startMenu = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                @"Microsoft\Windows\Start Menu\Programs", Program.AppName + ".lnk");
            Program.CreateShortcut(startMenu, exe, dest, ico);

            if (_desktop.Checked)
            {
                string desk = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                    Program.AppName + ".lnk");
                Program.CreateShortcut(desk, exe, dest, ico);
            }

            if (_startup.Checked)
            {
                string startup = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.Startup),
                    Program.AppName + ".lnk");
                Program.CreateShortcut(startup, exe, dest, ico);
            }

            File.WriteAllText(Path.Combine(dest, ".installed"),
                "Crownfall SubHub " + _version + "\n" + DateTime.Now.ToString("o"), Encoding.UTF8);

            Program.RegisterUninstall(dest, _version, Application.ExecutablePath);

            _bar.Value = 100;
            _status.ForeColor = Ui.Accent;
            _status.Text = "✓ Установлено: " + dest;

            if (_launch.Checked)
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = exe,
                    WorkingDirectory = dest,
                    UseShellExecute = true,
                });
            }

            _done = true;
            _install.Text = "Готово";
            _install.Enabled = true;
            _cancel.Visible = false;
        }
        catch (Exception ex)
        {
            _status.ForeColor = Ui.Danger;
            _status.Text = "Ошибка установки";
            MessageBox.Show(ex.Message, "Ошибка установки", MessageBoxButtons.OK, MessageBoxIcon.Error);
            _install.Enabled = true;
            _browse.Enabled = true;
            _dir.Enabled = true;
        }
    }
}

// ── Удаление ─────────────────────────────────────────────────────────────────
internal sealed class UninstallForm : DarkForm
{
    readonly string _installDir;
    readonly CheckBox _wipeData;
    readonly SlimProgress _bar;
    readonly Label _status;
    readonly Button _remove;
    readonly Button _cancel;
    bool _done;

    public UninstallForm(string version)
        : base("Удаление SubHub", version, 560, 400, Ui.Danger, Color.FromArgb(249, 115, 22))
    {
        Text = "Удаление SubHub " + version;
        _installDir = Program.ResolveInstallDir();

        var sub = new Label
        {
            Text = "Приложение будет удалено с компьютера",
            AutoSize = true,
            Left = 26, Top = 72,
            ForeColor = Ui.TextDim,
        };

        var dirLbl = new Label { Text = "Папка установки", Left = 24, Top = 112, AutoSize = true, ForeColor = Ui.TextDim };
        var dirVal = new Label
        {
            Text = _installDir,
            Left = 24, Top = 134, Width = 510, Height = 22,
            ForeColor = Ui.TextMain,
            Font = new Font("Consolas", 9.5f),
        };
        var what = new Label
        {
            Text = "Будут удалены: файлы приложения, ярлыки (рабочий стол, Пуск, автозапуск)\nи запись в «Приложениях» Windows.",
            Left = 24, Top = 164, Width = 510, Height = 36,
            ForeColor = Ui.TextDim,
        };

        _wipeData = Ui.MakeCheck("Также удалить данные и профили (data, chrome_profiles, secrets.yaml)", false, 24, 212);
        _wipeData.ForeColor = Ui.TextMain;
        _wipeData.CheckedChanged += delegate
        {
            _wipeData.ForeColor = _wipeData.Checked ? Ui.Danger : Ui.TextMain;
        };

        _bar = new SlimProgress { Left = 24, Top = 256, Width = 510, Visible = false };

        _status = new Label
        {
            Text = "",
            Left = 24, Top = 274, Width = 510, Height = 40,
            ForeColor = Ui.TextDim,
        };

        _remove = Ui.MakeButton("Удалить", Ui.Danger, Ui.DangerHover, Color.White, 150, 38);
        _remove.Left = 288;
        _remove.Top = 332;
        _remove.Click += delegate { OnRemoveClick(); };

        _cancel = Ui.MakeButton("Отмена", Ui.Field, Ui.Line, Color.White, 86, 38);
        _cancel.Left = 448;
        _cancel.Top = 332;
        _cancel.Click += delegate { Close(); };

        Controls.Add(sub);
        Controls.Add(dirLbl);
        Controls.Add(dirVal);
        Controls.Add(what);
        Controls.Add(_wipeData);
        Controls.Add(_bar);
        Controls.Add(_status);
        Controls.Add(_remove);
        Controls.Add(_cancel);
        CancelButton = _cancel;
    }

    void OnRemoveClick()
    {
        if (_done) { Close(); return; }

        bool wipe = _wipeData.Checked;
        if (wipe)
        {
            // Удаление сохранённых профилей — только с явным подтверждением
            var r = MessageBox.Show(
                "Точно удалить данные и сохранённые профили?\n\n" +
                _installDir + "\\data\n" + _installDir + "\\chrome_profiles\n\n" +
                "Это действие необратимо.",
                "Подтверждение удаления данных",
                MessageBoxButtons.YesNo, MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2);
            if (r != DialogResult.Yes)
                return;
        }

        try
        {
            _remove.Enabled = false;
            _wipeData.Enabled = false;
            _bar.Visible = true;
            _bar.Value = 20;
            _status.Text = "Закрытие SubHub и удаление файлов…";
            Application.DoEvents();

            Program.UninstallCore(_installDir, wipe);

            _bar.Value = 100;
            _status.ForeColor = Ui.Accent;
            _status.Text = wipe
                ? "✓ SubHub и все данные удалены"
                : "✓ SubHub удалён · данные сохранены: " + _installDir;

            Program.ScheduleSelfDelete(_installDir, wipe);

            _done = true;
            _remove.Text = "Закрыть";
            _remove.BackColor = Ui.Field;
            _remove.FlatAppearance.MouseOverBackColor = Ui.Line;
            _remove.Enabled = true;
            _cancel.Visible = false;
        }
        catch (Exception ex)
        {
            _status.ForeColor = Ui.Danger;
            _status.Text = "Ошибка удаления";
            MessageBox.Show(ex.Message, "Ошибка удаления", MessageBoxButtons.OK, MessageBoxIcon.Error);
            _remove.Enabled = true;
            _wipeData.Enabled = true;
        }
    }
}
