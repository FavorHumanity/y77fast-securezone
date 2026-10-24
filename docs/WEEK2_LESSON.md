# Week 2: Python Foundations, Applied to SecureZone

**Simulation only. Fictional data. Not vehicle software.**

---

## 1. What was broken

34 tests passed. Six bugs were still hiding.

- **Bug 1 (high):** A bad position (`NaN`, infinity, latitude 91) turned the speed cap **off**. 35 mph stayed 35 mph.
- **Bug 2 (high):** A signed map with one bad value (a limit of `"abc"`, a time of `"garbage"`) **crashed** the program instead of falling back to the safe limit.
- **Bug 3:** Times like `"7:00"` or `"25:99"` were accepted. A zone could silently never turn on.
- **Bug 4:** With overlapping zones, `check` and `cap` could disagree.
- **Bug 5:** The command line crashed on `--time garbage` or an unreadable key file.
- **Bug 6:** One test said "on the edge" but tested an inside point.

## 2. Why fail-open is dangerous

- **Fail-open:** something goes wrong, and the safety check turns itself off.
- **Fail-safe:** something goes wrong, and the system picks the safe answer (here, the lowest school limit, 20 mph).
- A safety tool that stops working when its input is odd is worst exactly when it is needed.
- Passing tests only prove the cases you thought of. Bad input is the case you did not think of.

## 3. How it was fixed

1. **Check the position first.** Is it a finite number? Is latitude within -90 to 90 and longitude within -180 to 180? If not, fail safe.
2. **Wrap map loading.** Any error while loading a map means "map unavailable", which means fail safe. No crash.
3. **Strict times.** Only `HH:MM` with zero padding, such as `07:00`.
4. **Overlapping zones.** An active zone wins. The lowest limit wins.
5. **Command line.** A bad `--time` is rejected with a clear message. An unreadable key means fail safe.
6. **Bad settings are rejected.** A `NaN` fallback limit or a broken area now raises an error at start-up.
7. **No NaN in signed data.** The signing format refuses `NaN` and infinity.

## 4. Python skills used (Week 2 topics)

- **Types:** `int`, `float`, `bool`. A `bool` is also an `int`, so check for it.
- **Control flow:** early `if` checks before the real work.
- **Functions:** small, single-purpose, easy to test.
- **Data structures:** `dict` and `list` shapes must be checked before use.
- **Modules:** `math.isfinite`, `json`, `datetime`.
- **Files:** reading JSON that may be missing or broken.

## 5. Five exercises

1. In Python, run `float("nan") > 5`, `float("nan") < 5` and `float("nan") == float("nan")`. Write down all three results. Why can `NaN` fool an `if` check?
2. Write `is_valid_position(lat, lon)` that returns `True` only for finite numbers in range. Test it with `nan`, `inf`, `91`, `-181`, `True`, `"12"` and `None`.
3. Write `parse_hhmm(text)` that accepts only `07:00` style and raises `ValueError` otherwise. Test `7:00`, `25:99` and `07:00:00`.
4. Write `safe_cap(speed, lat, lon)` that returns 20 whenever anything is wrong, and never raises. Add 8 tests, each with a bad input.
5. Write a 10-line CLI that reads a JSON file with `limit_mph`. If the file is missing, broken, or the limit is not a number, print `FAIL-SAFE: 20` and exit. Never show a traceback.

## 6. Check yourself

- Did every bad input end in the safe answer?
- Did any test only cover the "happy path"?
- Who is hurt if this fails open? Where in the code does it decide?

---
<sub>© 2026 Favor Figota Taueva · Y77FAST SOFTWARE FOUNDATION Corp. · All rights reserved.</sub>
