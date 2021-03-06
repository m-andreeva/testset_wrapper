from libtestset.constants import UnitConversion as Units
from os import chdir, environ, getcwd, scandir
from os.path import isfile, join
from pathlib import Path
from random import choice
from shutil import copy2, rmtree
from string import ascii_lowercase
from subprocess import CalledProcessError

import numpy as np
import subprocess


class DFTBPlusRunnerError(Exception):
    """Error raised when DFTB+ calculation goes wrong."""

    def __init__(self, exec_dir, msg=None):
        """Describes how to raise the error."""
        if not msg:
            msg = ("DFTB+ crashed with an unknown error!\n\n"
                   "Crashed calculation in: %s" % exec_dir)
        else:
            msg += "\nCrashed calculation in: %s" % exec_dir
        super().__init__(msg)


class XYZError(Exception):
    """Error raised when the input XYZ file is corrupt."""
    pass


class DFTBPlusDriver(object):
    """Runs DFTB+ calculations.

    object that runs DFTB+ calculations (with existing input file) and parses
    logs, including error checking.

    Inputs for instantiation:
    @:param executable: Path to the executable DFTB+ binary.
    @:param hsd_path: Path to the dftb_in.hsd template.
    @:param xyz: Path to xyz geometry file.
    @:param exec_dir: Directory to run the calculation in.
    """

    def __init__(self, executable, hsd_path, xyz, exec_dir):
        self.exe = executable
        self.hsd = hsd_path
        self.xyz = xyz
        self.exec_dir = exec_dir
        self.base_dir = getcwd()
        self._energy = None
        self._atoms = []
        self._coords = []

    def run(self):
        """Sets up the directory and runs the DFTB+ calculation."""
        Path(self.exec_dir).mkdir(parents=True, exist_ok=True)
        self._write_inputs()
        chdir(self.exec_dir)
        with open("dftbplus_output.log", "w") as fid:
            try:
                subprocess.run([self.exe], stdout=fid, stderr=fid, env=environ,
                               cwd=getcwd(), check=True, shell=True)
            except CalledProcessError:
                msg = "DFTB+ crashed on runtime, please check your input file!"
                raise DFTBPlusRunnerError(self.exec_dir, msg)
        with open("dftbplus_output.log", "r") as fid:
            for line in fid:
                if "Error" in line or "ERROR" in line:
                    raise DFTBPlusRunnerError(self.exec_dir)
                elif "SCC is NOT converged" in line:
                    msg = ("Self-Consistent-Charge (SCC) calculation did not "
                           "converge, check your geometry or convergence "
                           "criteria!")
                    raise DFTBPlusRunnerError(self.exec_dir, msg)
                elif "Geometry did NOT converge" in line:
                    msg = ("Geometry did not converge, check your "
                           "geometry or convergence criteria!")
                    raise DFTBPlusRunnerError(self.exec_dir, msg)
        self._parse_log()
        chdir(self.base_dir)

    @property
    def energy(self):
        """Returns calculated system energy in kcal/mol.

        @:returns float.
        """
        return self._energy

    @property
    def coordinates(self):
        """Returns the optimized geometry vectors.

        @:returns numpy Nx3 ndarray.
        """
        return self._coords

    @property
    def atoms(self):
        """Returns the geometry atom list.

        @:returns list of strings.
        """
        return self._atoms

    @classmethod
    def xyz2gen(cls, xyz, target):
        """Converts a xyz-geometry to gen format.

        Inputs:
        @:param xyz: Path to xyz file.
        @:param target: Path where gen file should be written to.
        """
        geom = cls.read_xyz(xyz)
        n_atoms = len(geom)
        atoms = []
        for at, vec in geom:
            if at not in atoms:
                atoms.append(at)
        # Create gen file, line by line
        gen_file = ["%s C" % n_atoms, " ".join(atoms)]
        at_dict = dict()
        for idx, at in enumerate(atoms):
            at_dict[at] = idx + 1
        for idx, entry in enumerate(geom):
            at = entry[0]
            vec = entry[1]
            gen_file.append("%s %s   %s" % (idx + 1, at_dict[at], vec))
        # Write gen file
        with open(target, "w") as out:
            for line in gen_file:
                out.write("%s\n" % line)

    @staticmethod
    def read_xyz(xyz):
        """Reads a xyz-file into a list of (atom, vector) tuples.

        Note:
        The given vector is just a string, not a real float vector!

        Inputs:
        @:param xyz: Path to xyz file.

        @:return geom: List of (atom, vector) tuples.
        """
        with open(xyz, "r") as xyz_in:
            n_atoms = int(xyz_in.readline())
            xyz_in.readline()
            geom = []
            for line in xyz_in:
                splt = line.split()
                if len(splt) != 4:
                    continue
                at = splt[0]
                vec_str = "   ".join(splt[1:])
                geom.append((at, vec_str))
        if n_atoms != len(geom):
            msg = ("XYZ geometry file %s corrupt! Number of atoms in line one "
                   "does not match number of given coordinate vectors.")
            raise XYZError(msg % xyz)
        return geom

    def _write_inputs(self):
        """Writes input for DFTB+ calculation."""
        copy2(self.hsd, self.exec_dir)
        self.xyz2gen(self.xyz, join(self.exec_dir, "in.gen"))

    def _parse_log(self):
        """Reads DFTB+ detailed.out and sets needed values in object."""
        with open("detailed.out", "r") as log:
            for line in log:
                if "Total energy:" in line:
                    splt = line.split()
                    self._energy = float(splt[2]) * Units.au2kcal
        if not isfile("geo_end.xyz"):
            msg = ("DFTB+ calculations in %s did not produce a geometry output "
                   "file. Use the 'Driver' option in the DFTB+ input file for "
                   "geometry optimizations!")
            raise DFTBPlusRunnerError(msg % self.exec_dir)
        vecs = []  # will be coordinates
        with open("geo_end.xyz", "r") as xyz:
            for line in xyz:
                splt = line.split()
                if len(splt) != 5:
                    continue
                vecs.append([float(splt[1]), float(splt[2]), float(splt[3])])
                self._atoms.append(splt[0])
        self._coords = np.asarray(vecs)


def run_testset(set_definition, hsd, executable):
    """Runs all systems in a given testset.

    Inputs:
    @:param set_definition: dictionary describing the testset as given in input
        file.
    @:param hsd: Path to dftb_in.hsd
    @:param executable: Path to DFTB+ executable.

    @:returns Dictionary of systems and their total energies in kcal/mol.
    """
    set_path = join(getcwd(), set_definition["path"])
    systems = dict()
    for entry in scandir(set_path):
        if entry.is_file() and entry.name.endswith(".xyz"):
            sys_name = entry.name[:-4]
            xyz = entry.path
            exec_dir = get_random_folder(prefix="dftb+_run_")
            driver = DFTBPlusDriver(executable, hsd, xyz, exec_dir)
            driver.run()
            systems[sys_name] = driver
            rmtree(exec_dir)
    return systems


def get_random_folder(prefix="", length=8):
    random_string = "".join(choice(ascii_lowercase) for _i in range(length))
    return prefix + random_string


if __name__ == "__main__":
    pass
