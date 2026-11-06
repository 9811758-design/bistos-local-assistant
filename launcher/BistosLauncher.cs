using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Net;
using System.Reflection;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("비스토스 제품도우미")]
[assembly: AssemblyProduct("비스토스 제품도우미")]
[assembly: AssemblyDescription("기존 비스토스 매뉴얼 도우미 실행 프로그램")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace BistosLauncher
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            bool check = Array.IndexOf(args, "--check") >= 0;
            bool headless = Array.IndexOf(args, "--no-browser") >= 0;
            bool preview = args.Length == 2 && args[0] == "--preview";
            string root = Launcher.ResolveRoot();
            if (root == null || !Launcher.HasLogo())
            {
                if (!check && !headless && !preview)
                    MessageBox.Show("프로그램 설치 파일을 찾을 수 없습니다. 기존 프로그램 폴더를 확인해 주세요.",
                        "비스토스 제품도우미", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
            if (check) return 0;
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            if (preview)
            {
                using (LauncherForm form = new LauncherForm(root, true))
                using (Bitmap image = new Bitmap(form.Width, form.Height))
                {
                    form.ShowInTaskbar = false;
                    form.StartPosition = FormStartPosition.Manual;
                    form.Location = new Point(-32000, -32000);
                    form.Show();
                    Application.DoEvents();
                    form.DrawToBitmap(image, new Rectangle(Point.Empty, image.Size));
                    image.Save(args[1], ImageFormat.Png);
                    form.Close();
                }
                return 0;
            }
            bool created;
            using (Mutex mutex = new Mutex(true, Launcher.MutexName(root), out created))
            {
                if (!created)
                {
                    if (!headless)
                        MessageBox.Show("프로그램을 준비하고 있습니다. 잠시만 기다려 주세요.",
                            "비스토스 제품도우미", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    return 0;
                }
                try
                {
                    if (headless) return Launcher.Start(root) == null ? 0 : 1;
                    using (LauncherForm form = new LauncherForm(root, false))
                    {
                        Application.Run(form);
                        return form.ExitCode;
                    }
                }
                finally { mutex.ReleaseMutex(); }
            }
        }
    }

    internal static class Launcher
    {
        internal static string ResolveRoot()
        {
            string adjacent = AppDomain.CurrentDomain.BaseDirectory;
            if (ValidRoot(adjacent)) return Path.GetFullPath(adjacent).TrimEnd('\\');
            using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(
                "BistosLauncher.InstallationRoot.txt"))
            {
                if (stream == null) return null;
                using (StreamReader reader = new StreamReader(stream, Encoding.UTF8))
                {
                    string installed = reader.ReadToEnd().Trim();
                    return ValidRoot(installed) ? Path.GetFullPath(installed).TrimEnd('\\') : null;
                }
            }
        }

        private static bool ValidRoot(string root)
        {
            if (string.IsNullOrEmpty(root) || !Path.IsPathRooted(root)) return false;
            foreach (string relative in new[] { "scripts\\start.ps1", "bistos\\server.py", ".venv\\Scripts\\python.exe" })
                if (!File.Exists(Path.Combine(root, relative))) return false;
            return true;
        }

        internal static Bitmap LoadLogo()
        {
            using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(
                "BistosLauncher.BistosLogo.png"))
            using (Image image = Image.FromStream(stream))
                return new Bitmap(image);
        }

        internal static bool HasLogo()
        {
            try { using (Bitmap image = LoadLogo()) return image.Width > 0 && image.Height > 0; }
            catch { return false; }
        }

        internal static string MutexName(string root)
        {
            string sid = WindowsIdentity.GetCurrent().User.Value;
            using (SHA256 sha = SHA256.Create())
                return "Local\\BistosLauncher-" + BitConverter.ToString(sha.ComputeHash(
                    Encoding.UTF8.GetBytes(root.ToLowerInvariant() + "|" + sid))).Replace("-", "");
        }

        internal static string Start(string root)
        {
            try
            {
                string system = Environment.GetFolderPath(Environment.SpecialFolder.System);
                string powershell = Path.Combine(system, "WindowsPowerShell\\v1.0\\powershell.exe");
                ProcessStartInfo info = new ProcessStartInfo(powershell,
                    "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" +
                    Path.Combine(root, "scripts\\start.ps1") + "\" -NoBrowser");
                info.WorkingDirectory = root;
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                info.RedirectStandardOutput = true;
                info.RedirectStandardError = true;
                info.EnvironmentVariables["PSModulePath"] =
                    Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                        "WindowsPowerShell\\Modules") + ";" +
                    Path.Combine(system, "WindowsPowerShell\\v1.0\\Modules");
                using (Process process = new Process { StartInfo = info })
                {
                    // Drain both streams concurrently without logging raw service output.
                    process.OutputDataReceived += delegate { };
                    process.ErrorDataReceived += delegate { };
                    process.Start();
                    process.BeginOutputReadLine();
                    process.BeginErrorReadLine();
                    if (!process.WaitForExit(150000))
                    {
                        // Stop only this launch command; leave app/AI/tunnel processes intact.
                        try { process.Kill(); } catch (InvalidOperationException) { }
                        Log(root, "Startup command timed out.");
                        return "준비 시간이 길어지고 있습니다. 잠시 후 다시 실행해 주세요.";
                    }
                    if (process.ExitCode != 0)
                    {
                        Log(root, "Startup command failed, exit code " + process.ExitCode + ".");
                        return "프로그램을 시작하지 못했습니다. 잠시 후 다시 실행해 주세요.";
                    }
                }
                for (int attempt = 0; attempt < 6; attempt++)
                {
                    if (Ready()) return null;
                    Thread.Sleep(500);
                }
                Log(root, "Local application readiness check failed.");
                return "프로그램 연결을 확인하지 못했습니다. 잠시 후 다시 실행해 주세요.";
            }
            catch (Exception error)
            {
                Log(root, "Launch failed: " + error.GetType().Name + ".");
                return "프로그램 실행 중 오류가 발생했습니다. 기존 프로그램 폴더를 확인해 주세요.";
            }
        }

        private static bool Ready()
        {
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:8765/api/status");
                request.Proxy = null;
                request.AllowAutoRedirect = false;
                request.Timeout = 1500;
                request.ReadWriteTimeout = 1500;
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                using (StreamReader reader = new StreamReader(response.GetResponseStream()))
                {
                    if (response.StatusCode != HttpStatusCode.OK ||
                        !response.ContentType.StartsWith("application/json", StringComparison.OrdinalIgnoreCase))
                        return false;
                    char[] buffer = new char[8193];
                    int count = reader.ReadBlock(buffer, 0, buffer.Length);
                    if (count == buffer.Length) return false;
                    Dictionary<string, object> status = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(
                        new string(buffer, 0, count));
                    return status.ContainsKey("setup_required") && status["setup_required"] is bool &&
                        !(bool)status["setup_required"] && status.ContainsKey("model_ready");
                }
            }
            catch { return false; }
        }

        internal static void Log(string root, string message)
        {
            try
            {
                File.AppendAllText(Path.Combine(root, "data\\exe-launcher.log"),
                    DateTime.Now.ToString("o") + " " + message + Environment.NewLine, Encoding.UTF8);
            }
            catch { }
        }
    }

    internal sealed class LauncherForm : Form
    {
        private readonly Label status;
        private readonly ProgressBar progress;
        private readonly Button close;
        private readonly Bitmap logo;
        internal int ExitCode { get; private set; }

        internal LauncherForm(string root, bool preview)
        {
            ExitCode = 1;
            Text = "비스토스 제품도우미";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            ClientSize = new Size(480, 280);
            BackColor = Color.White;
            Font = new Font("Malgun Gothic", 10F);
            AutoScaleMode = AutoScaleMode.Dpi;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
            logo = Launcher.LoadLogo();
            Controls.Add(new PictureBox {
                Image = logo, SizeMode = PictureBoxSizeMode.Zoom,
                Bounds = new Rectangle(120, 26, 240, 76), BackColor = Color.White
            });
            Controls.Add(new Label {
                Text = "제품 지식도우미", Font = new Font("Malgun Gothic", 18F, FontStyle.Bold),
                ForeColor = Color.FromArgb(29, 53, 59), TextAlign = ContentAlignment.MiddleCenter,
                Bounds = new Rectangle(20, 118, 440, 38)
            });
            status = new Label {
                Text = "프로그램을 준비하고 있습니다…", TextAlign = ContentAlignment.MiddleCenter,
                ForeColor = Color.FromArgb(91, 109, 116), Bounds = new Rectangle(24, 168, 432, 44)
            };
            progress = new ProgressBar {
                Style = ProgressBarStyle.Marquee, MarqueeAnimationSpeed = 24,
                Bounds = new Rectangle(90, 228, 300, 8)
            };
            close = new Button { Text = "닫기", Bounds = new Rectangle(190, 225, 100, 32), Visible = false };
            close.Click += delegate { Close(); };
            Controls.Add(status);
            Controls.Add(progress);
            Controls.Add(close);
            if (!preview)
            {
                Shown += delegate {
                    BackgroundWorker worker = new BackgroundWorker();
                    worker.DoWork += delegate(object sender, DoWorkEventArgs e) { e.Result = Launcher.Start(root); };
                    worker.RunWorkerCompleted += delegate(object sender, RunWorkerCompletedEventArgs e) {
                        worker.Dispose();
                        if (IsDisposed) return;
                        string failure = e.Error == null ? (string)e.Result : "프로그램을 시작하지 못했습니다.";
                        if (failure == null)
                        {
                            try { Process.Start(new ProcessStartInfo("http://127.0.0.1:8765") { UseShellExecute = true }); }
                            catch { failure = "브라우저를 열지 못했습니다. http://127.0.0.1:8765로 접속해 주세요."; }
                        }
                        if (failure == null) { ExitCode = 0; Close(); }
                        else { status.Text = failure; progress.Visible = false; close.Visible = true; }
                    };
                    worker.RunWorkerAsync();
                };
            }
        }

        protected override void Dispose(bool disposing)
        {
            base.Dispose(disposing);
            if (disposing) logo.Dispose();
        }
    }
}
