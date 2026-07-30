def test_the_dublicate_typo_never_returns():
	import os
	root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	hits = []
	for base in ("bot", "core", "utils"):
		for dirpath, _d, files in os.walk(os.path.join(root, base)):
			if "__pycache__" in dirpath:
				continue
			for f in files:
				if f.endswith(".py"):
					with open(os.path.join(dirpath, f), encoding="utf-8") as fp:
						if "on_dublicate" in fp.read():
							hits.append(os.path.join(dirpath, f))
	assert hits == []
