from __future__ import annotations

try:
    from importlib.metadata import PackageNotFoundError  # type: ignore
    from importlib.metadata import version
except ImportError:  # pragma: no cover
    from importlib_metadata import PackageNotFoundError  # type: ignore
    from importlib_metadata import version


try:
    __version__ = version(__name__)
except PackageNotFoundError:  # pragma: no cover
    __version__ = "unknown"

# documentation format
__author__ = "Mostafa Farrag"
__email__ = "moah.farag@gmail.com"
__docformat__ = "restructuredtext"

# Let users know if they're missing any of our hard dependencies
hard_dependencies = ()  # ("numpy", "pandas", "gdal")
missing_dependencies = []

for dependency in hard_dependencies:
    try:
        __import__(dependency)
    except ImportError as e:
        missing_dependencies.append(dependency)
        print(e)

if missing_dependencies:
    raise ImportError("Missing required dependencies {0}".format(missing_dependencies))

# import earthly.gee.data as data
# import earthly.gee.dataset as dataset
# import earthly.gee.features as features
# import earthly.gee.gee as gee
# import earthly.gee.images as images

__doc__ = """
gee - google earth engine
"""
