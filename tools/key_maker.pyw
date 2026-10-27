"""AltVault key maker: double-click this file, paste what someone sent you, click Make key.

It reads the PC code out of their message, makes the key with your private signing key (kept in
~/Tools/AltVault-signing, outside this repository), copies the key so you can paste it straight into
your reply, and notes who it's for. Nothing is sent anywhere. Not part of the AltVault app.
"""
import os
import sys
import tkinter as tk
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import license_tool as tool   # noqa: E402

LASTS = (("Forever", None), ("7 days", 7), ("30 days", 30), ("90 days", 90))


class KeyMaker(tk.Tk):
    def __init__(self, directory=None, copy=tool.copy_to_clipboard):
        super().__init__()
        self.directory, self.copy = directory or tool.DEFAULT_DIR, copy
        self.app = tool._altvault()
        app = self.app
        scale = self.winfo_fpixels("1i") / 96
        app.apply_theme(self, scale)
        self.title("AltVault key maker")
        self.resizable(False, False)
        frm = ttk.Frame(self, padding=(int(26 * scale), int(22 * scale)))
        frm.pack(fill="both", expand=True)
        wrap = int(460 * scale)
        ttk.Label(frm, text="Make an AltVault key", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frm, text="Paste what the person sent you. It can be their whole message; the PC code is picked out of it.",
                  wraplength=wrap, justify="left").pack(anchor="w", pady=(8, 0))
        self.code_var = tk.StringVar()
        code = ttk.Entry(frm, textvariable=self.code_var)
        code.pack(fill="x", pady=(10, 0))
        app.add_placeholder(code, self.code_var, "Paste their message or PC code here")
        ttk.Label(frm, text="Who is it for? (only for your own records)", style="Muted.TLabel").pack(anchor="w", pady=(14, 3))
        self.name_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.name_var).pack(fill="x")
        row = ttk.Frame(frm)
        row.pack(fill="x", pady=(14, 0))
        ttk.Label(row, text="Key lasts", style="Muted.TLabel").pack(side="left")
        self.lasts_var = tk.StringVar(value=LASTS[0][0])
        ttk.Combobox(row, textvariable=self.lasts_var, values=[n for n, _ in LASTS], state="readonly",
                     width=10).pack(side="left", padx=(8, 0))
        self.status = ttk.Label(frm, text="", wraplength=wrap, justify="left", style="Muted.TLabel")
        self.status.pack(anchor="w", pady=(12, 0))
        self.key_var = tk.StringVar()
        self.key_box = ttk.Entry(frm, textvariable=self.key_var, state="readonly")
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="Make key", style="Accent.TButton", command=self.make).pack(side="right")
        ttk.Button(btns, text="Who have I approved?", command=self.show_ledger).pack(side="left")
        code.bind("<Return>", lambda _e: self.make())
        self.update_idletasks()
        self.geometry(f"+{(self.winfo_screenwidth() - self.winfo_reqwidth()) // 2}+{(self.winfo_screenheight() - self.winfo_reqheight()) // 3}")
        app.dark_title_bar(self)
        code.focus_set()

    def make(self):
        pc = tool.find_pc_code(self.code_var.get())
        if not pc:
            return self.say("I can't find a PC code in that. It looks like ABCD-EFGH-JKLM-NPQR.", error=True)
        days = dict(LASTS)[self.lasts_var.get()]
        try:
            key = tool.issue_key(self.directory, pc, self.name_var.get().strip(), days, app=self.app)
        except (FileNotFoundError, ValueError) as e:
            return self.say(str(e), error=True)
        self.copy(key)
        self.key_var.set(key)
        self.key_box.pack(fill="x", pady=(12, 0), after=self.status)
        self.say(f"Key made for {pc} and copied. Paste it into your reply.", ok=True)

    def say(self, text, error=False, ok=False):
        self.status.config(text=text, style="Error.TLabel" if error else "Success.TLabel" if ok else "Muted.TLabel")

    def show_ledger(self):
        win = tk.Toplevel(self, bg=self.app.C["bg"])
        win.title("Keys you've made")
        text = tk.Text(win, width=70, height=16, bg=self.app.C["panel"], fg=self.app.C["fg"], bd=0, padx=12, pady=10,
                       font=("Consolas", 10), highlightthickness=0, wrap="none")
        text.insert("1.0", tool.read_ledger(self.directory).replace(",", "   "))
        text.config(state="disabled")
        text.pack(fill="both", expand=True)
        self.app.dark_title_bar(win)


if __name__ == "__main__":
    KeyMaker().mainloop()
