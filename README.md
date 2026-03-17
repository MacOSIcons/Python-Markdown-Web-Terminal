# Python Markdown Browser with Web Terminal

A Flask-based Markdown browser combined with a web terminal.

---

# 🚀 Getting Started

## 1. Install Dependencies

```bash
pip install flask markdown pymdown-extensions pygments
```

## 2. Run

```bash
python3 app.py
```

Open in browser:

```
http://localhost:5000
```

---

# 📦 Features

* 📁 Browse server directories
* 📝 Render Markdown (math, code highlight, tabs, etc.)
* 💻 Real bash terminal in browser
* 🔄 Auto-sync working directory
* ✨ UI effects (cards, glow, animation)

---

# ✅ Current Version Notes

* UI preserved
* particles.js removed (performance improved)
* Enhanced Markdown support
* Terminal sync works correctly

---

# 🧨 Most Common Pitfall

👉 **99% of issues come from this:**

```html
onclick="loadFragment('{{ xxx }}')"
```

Must include:

```html
return false;
```

---

# 🧩 Dependencies

| Package            | Required |
| ------------------ | -------- |
| flask              | ✅        |
| markdown           | ✅        |
| pymdown-extensions | ✅        |
| pygments           | ✅        |

---

# 📌 Summary

If something breaks:

1. Check browser console
2. Check Flask logs
3. Verify paths
