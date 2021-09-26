from contextlib import contextmanager
import unittest

from clcache.cmdline import *
from clcache.errors import (
    AnalysisError,
)

# TODO: should be shared between unit tests
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
@contextmanager
def cd(targetDirectory):
    oldDirectory = os.getcwd()
    os.chdir(os.path.expanduser(targetDirectory))
    try:
        yield
    finally:
        os.chdir(oldDirectory)


class TestArgumentClasses(unittest.TestCase):
    def testEquality(self):
        self.assertEqual(ArgumentT1('Fo'), ArgumentT1('Fo'))
        self.assertEqual(ArgumentT1('W'), ArgumentT1('W'))
        self.assertEqual(ArgumentT2('W'), ArgumentT2('W'))
        self.assertEqual(ArgumentT3('W'), ArgumentT3('W'))
        self.assertEqual(ArgumentT4('W'), ArgumentT4('W'))

        self.assertNotEqual(ArgumentT1('Fo'), ArgumentT1('W'))
        self.assertNotEqual(ArgumentT1('Fo'), ArgumentT1('FO'))

        self.assertNotEqual(ArgumentT1('W'), ArgumentT2('W'))
        self.assertNotEqual(ArgumentT2('W'), ArgumentT3('W'))
        self.assertNotEqual(ArgumentT3('W'), ArgumentT4('W'))
        self.assertNotEqual(ArgumentT4('W'), ArgumentT1('W'))

    def testHash(self):
        self.assertEqual(hash(ArgumentT1('Fo')), hash(ArgumentT1('Fo')))
        self.assertEqual(hash(ArgumentT1('W')), hash(ArgumentT1('W')))
        self.assertEqual(hash(ArgumentT2('W')), hash(ArgumentT2('W')))
        self.assertEqual(hash(ArgumentT3('W')), hash(ArgumentT3('W')))
        self.assertEqual(hash(ArgumentT4('W')), hash(ArgumentT4('W')))

        self.assertNotEqual(hash(ArgumentT1('Fo')), hash(ArgumentT1('W')))
        self.assertNotEqual(hash(ArgumentT1('Fo')), hash(ArgumentT1('FO')))

        self.assertNotEqual(hash(ArgumentT1('W')), hash(ArgumentT2('W')))
        self.assertNotEqual(hash(ArgumentT2('W')), hash(ArgumentT3('W')))
        self.assertNotEqual(hash(ArgumentT3('W')), hash(ArgumentT4('W')))
        self.assertNotEqual(hash(ArgumentT4('W')), hash(ArgumentT1('W')))


class TestAnalyzeCommandLine(unittest.TestCase):
    def _testSourceFilesOk(self, cmdLine):
        try:
            CommandLineAnalyzer.analyze(cmdLine)
        except AnalysisError as err:
            if isinstance(err, NoSourceFileError):
                self.fail("analyze() unexpectedly raised an NoSourceFileError")
            else:
                # We just want to know if we got a proper source file.
                # Other AnalysisErrors are ignored.
                pass

    def _testFailure(self, cmdLine, expectedExceptionClass):
        self.assertRaises(expectedExceptionClass, lambda: CommandLineAnalyzer.analyze(cmdLine))

    def _testFull(self, cmdLine, expectedSourceFiles, expectedOutputFile):
        # type: (List[str], List[Tuple[str, str]], List[str]) -> None
        sourceFiles, outputFile = CommandLineAnalyzer.analyze(cmdLine)
        self.assertEqual(sourceFiles, expectedSourceFiles)
        self.assertEqual(outputFile, expectedOutputFile)

    def _testFo(self, foArgument, expectedObjectFilepath):
        self._testFull(['/c', foArgument, 'main.cpp'],
                       [("main.cpp", '')], [expectedObjectFilepath])

    def _testFi(self, fiArgument):
        self._testPreprocessingOutfile(['/c', '/P', fiArgument, 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/P', '/EP', fiArgument, 'main.cpp'])

    def _testPreprocessingOutfile(self, cmdLine):
        self._testFailure(cmdLine, CalledForPreprocessingError)

    def _testArgInfiles(self, cmdLine, expectedArguments, expectedInputFiles):
        arguments, inputFiles = CommandLineAnalyzer.parseArgumentsAndInputFiles(cmdLine)
        self.assertEqual(arguments, expectedArguments)
        self.assertEqual(inputFiles, expectedInputFiles)

    def testEmpty(self):
        self._testFailure([], NoSourceFileError)

    def testSimple(self):
        self._testFull(["/c", "main.cpp"], [("main.cpp", "")], ["main.obj"])

    def testNoSource(self):
        # No source file has priority over other errors, for consistency
        # and because it's likely to be a misconfigured command line.
        self._testFailure(['/c', '/nologo'], NoSourceFileError)
        self._testFailure(['/c'], NoSourceFileError)
        self._testFailure([], NoSourceFileError)
        self._testFailure(['/Zi'], NoSourceFileError)
        self._testFailure(['/E'], NoSourceFileError)
        self._testFailure(['/P'], NoSourceFileError)
        self._testFailure(['/EP'], NoSourceFileError)
        self._testFailure(['/Yc'], NoSourceFileError)
        self._testFailure(['/Yu'], NoSourceFileError)
        self._testFailure(['/link'], NoSourceFileError)

    def testOutputFileFromSourcefile(self):
        # For object file
        self._testFull(['/c', 'main.cpp'],
                       [('main.cpp', '')], ['main.obj'])
        # For preprocessor file
        self._testFailure(['/c', '/P', 'main.cpp'], CalledForPreprocessingError)

    def testPreprocessIgnoresOtherArguments(self):
        # All those inputs must ignore the /Fo, /Fa and /Fm argument according
        # to the documentation of /E, /P and /EP

        # to file (/P)
        self._testPreprocessingOutfile(['/c', '/P', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/P', '/FoSome.obj', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/P', '/FaListing.asm', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/P', '/FmMapfile.map', 'main.cpp'])

        # to file (/P /EP)
        # Note: documentation bug in https://msdn.microsoft.com/en-us/library/becb7sys.aspx
        self._testPreprocessingOutfile(['/c', '/P', '/EP', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/P', '/EP', '/FoSome.obj', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/P', '/EP', '/FaListing.asm', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/P', '/EP', '/FmMapfile.map', 'main.cpp'])

        # to stdout (/E)
        self._testPreprocessingOutfile(['/c', '/E', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/E', '/FoSome.obj', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/E', '/FaListing.asm', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/E', '/FmMapfile.map', 'main.cpp'])

        # to stdout (/EP)
        self._testPreprocessingOutfile(['/c', '/EP', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/EP', '/FoSome.obj', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/EP', '/FaListing.asm', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/EP', '/FmMapfile.map', 'main.cpp'])

    def testOutputFile(self):
        # Given object filename (default extension .obj)
        self._testFo('/FoTheOutFile.obj', 'TheOutFile.obj')

        # Given object filename (custom extension .dat)
        self._testFo('/FoTheOutFile.dat', 'TheOutFile.dat')

        # Given object filename (with spaces)
        self._testFo('/FoThe Out File.obj', 'The Out File.obj')

        # Existing directory
        with cd(ASSETS_DIR):
            self._testFo(r'/Fo.', r'.\main.obj')
            self._testFo(r'/Fofo-build-debug', r'fo-build-debug\main.obj')
            self._testFo(r'/Fofo-build-debug\\', r'fo-build-debug\main.obj')

    def testOutputFileNormalizePath(self):
        # Out dir does not exist, but preserve path. Compiler will complain
        self._testFo(r'/FoDebug\TheOutFile.obj', r'Debug\TheOutFile.obj')

        # Convert to Windows path separatores (like cl does too)
        self._testFo(r'/FoDebug/TheOutFile.obj', r'Debug\TheOutFile.obj')

        # Different separators work as well
        self._testFo(r'/FoDe\bug/TheOutFile.obj', r'De\bug\TheOutFile.obj')

        # Double slash
        self._testFo(r'/FoDebug//TheOutFile.obj', r'Debug\TheOutFile.obj')
        self._testFo(r'/FoDebug\\TheOutFile.obj', r'Debug\TheOutFile.obj')

    def testPreprocessingFi(self):
        # Given output filename
        self._testFi('/FiTheOutFile.i')
        self._testFi('/FiTheOutFile.dat')
        self._testFi('/FiThe Out File.i')

        # Existing directory
        with cd(ASSETS_DIR):
            self._testFi(r'/Fi.')
            self._testFi(r'/Fifi-build-debug')
            self._testFi(r'/Fifi-build-debug\\')

        # Non-existing directory: preserve path, compiler will complain
        self._testFi(r'/FiDebug\TheOutFile.i')

        # Convert to single Windows path separatores (like cl does too)
        self._testFi(r'/FiDebug/TheOutFile.i')
        self._testFi(r'/FiDe\bug/TheOutFile.i')
        self._testFi(r'/FiDebug//TheOutFile.i')
        self._testFi(r'/FiDebug\\TheOutFile.i')

    def testTpTcSimple(self):
        # clcache can handle /Tc or /Tp as long as there is only one of them
        self._testFull(['/c', '/TcMyCcProgram.c'],
                       [('MyCcProgram.c', '/Tc')], ['MyCcProgram.obj'])
        self._testFull(['/c', '/TpMyCxxProgram.cpp'],
                       [('MyCxxProgram.cpp', '/Tp')], ['MyCxxProgram.obj'])

    def testLink(self):
        self._testFailure(["main.cpp"], CalledForLinkError)
        self._testFailure(["/nologo", "main.cpp"], CalledForLinkError)

    def testArgumentParameters(self):
        # Type 1 (/NAMEparameter) - Arguments with required parameter
        self._testFailure(["/c", "/Ob", "main.cpp"], InvalidArgumentError)
        self._testFailure(["/c", "/Yl", "main.cpp"], InvalidArgumentError)
        self._testFailure(["/c", "/Zm", "main.cpp"], InvalidArgumentError)
        self._testSourceFilesOk(["/c", "/Ob999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Yl999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Zm999", "main.cpp"])

        # Type 2 (/NAME[parameter]) - Optional argument parameters must not eat up source file
        self._testSourceFilesOk(["/c", "/doc", "main.cpp"])
        self._testSourceFilesOk(["/c", "/FA", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Fr", "main.cpp"])
        self._testSourceFilesOk(["/c", "/FR", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Gs", "main.cpp"])
        self._testSourceFilesOk(["/c", "/MP", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Wv", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Yc", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Yu", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Zp", "main.cpp"])

        # Type 3 (/NAME[ ]parameter) - Required argument parameters with optional space eat up source file
        self._testFailure(["/c", "/FI", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/U", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/I", "main.cpp"], NoSourceFileError)
        self._testSourceFilesOk(["/c", "/FI9999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/U9999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/I9999", "main.cpp"])

        # Type 4 (/NAME parameter) - Forced space
        # Some documented, but non implemented
        self._testFailure(["/c", "/Xclang", "main.cpp"], NoSourceFileError)
        self._testSourceFilesOk(["/c", "/Xclang", "foo", "main.cpp"])

        # Documented as type 1 (/NAMEparmeter) but work as type 2 (/NAME[parameter])
        self._testSourceFilesOk(["/c", "/Fa", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Fi", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Fd", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Fe", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Fm", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Fo", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Fp", "main.cpp"])

        # Documented as type 1 (/NAMEparmeter) but work as type 3 (/NAME[ ]parameter)
        self._testFailure(["/c", "/AI", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/D", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/V", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/w1", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/w2", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/w3", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/w4", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/wd", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/we", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/wo", "main.cpp"], NoSourceFileError)
        self._testSourceFilesOk(["/c", "/AI999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/D999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/V999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/w1999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/w2999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/w3999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/w4999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/wd999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/we999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/wo999", "main.cpp"])
        # Those work a bit differently
        self._testSourceFilesOk(["/c", "/Tc", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Tp", "main.cpp"])
        self._testFailure(["/c", "/Tc", "999", "main.cpp"], MultipleSourceFilesComplexError)
        self._testFailure(["/c", "/Tp", "999", "main.cpp"], MultipleSourceFilesComplexError)
        self._testFailure(["/c", "/Tc999", "main.cpp"], MultipleSourceFilesComplexError)
        self._testFailure(["/c", "/Tp999", "main.cpp"], MultipleSourceFilesComplexError)

        # Documented as type 4 (/NAME parameter) but work as type 3 (/NAME[ ]parameter)
        self._testFailure(["/c", "/F", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/FU", "main.cpp"], NoSourceFileError)
        self._testSourceFilesOk(["/c", "/F999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/FU999", "main.cpp"])

    def testParseArgumentsAndInputFiles(self):
        self._testArgInfiles(['/c', 'main.cpp'],
                             {'c': ['']},
                             ['main.cpp'])
        self._testArgInfiles(['/link', 'unit1.obj', 'unit2.obj'],
                             {'link': ['']},
                             ['unit1.obj', 'unit2.obj'])
        self._testArgInfiles(['/Fooutfile.obj', 'main.cpp'],
                             {'Fo': ['outfile.obj']},
                             ['main.cpp'])
        self._testArgInfiles(['/Fo', '/Fooutfile.obj', 'main.cpp'],
                             {'Fo': ['', 'outfile.obj']},
                             ['main.cpp'])
        self._testArgInfiles(['/c', '/I', 'somedir', 'main.cpp'],
                             {'c': [''], 'I': ['somedir']},
                             ['main.cpp'])
        self._testArgInfiles(['/c', '/I.', '/I', 'somedir', 'main.cpp'],
                             {'c': [''], 'I': ['.', 'somedir']},
                             ['main.cpp'])

        # Type 1 (/NAMEparameter) - Arguments with required parameter
        # get parameter=99
        self._testArgInfiles(["/c", "/Ob99", "main.cpp"], {'c': [''], 'Ob': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Yl99", "main.cpp"], {'c': [''], 'Yl': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Zm99", "main.cpp"], {'c': [''], 'Zm': ['99']}, ['main.cpp'])

        # # Type 2 (/NAME[parameter]) - Optional argument parameters
        # get parameter=99
        self._testArgInfiles(["/c", "/doc99", "main.cpp"], {'c': [''], 'doc': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/FA99", "main.cpp"], {'c': [''], 'FA': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fr99", "main.cpp"], {'c': [''], 'Fr': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/FR99", "main.cpp"], {'c': [''], 'FR': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Gs99", "main.cpp"], {'c': [''], 'Gs': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/MP99", "main.cpp"], {'c': [''], 'MP': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Wv99", "main.cpp"], {'c': [''], 'Wv': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Yc99", "main.cpp"], {'c': [''], 'Yc': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Yu99", "main.cpp"], {'c': [''], 'Yu': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Zp99", "main.cpp"], {'c': [''], 'Zp': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fa99", "main.cpp"], {'c': [''], 'Fa': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fd99", "main.cpp"], {'c': [''], 'Fd': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fe99", "main.cpp"], {'c': [''], 'Fe': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fi99", "main.cpp"], {'c': [''], 'Fi': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fm99", "main.cpp"], {'c': [''], 'Fm': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fo99", "main.cpp"], {'c': [''], 'Fo': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fp99", "main.cpp"], {'c': [''], 'Fp': ['99']}, ['main.cpp'])
        # get no parameter
        self._testArgInfiles(["/c", "/doc", "main.cpp"], {'c': [''], 'doc': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/FA", "main.cpp"], {'c': [''], 'FA': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fr", "main.cpp"], {'c': [''], 'Fr': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/FR", "main.cpp"], {'c': [''], 'FR': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Gs", "main.cpp"], {'c': [''], 'Gs': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/MP", "main.cpp"], {'c': [''], 'MP': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Wv", "main.cpp"], {'c': [''], 'Wv': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Yc", "main.cpp"], {'c': [''], 'Yc': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Yu", "main.cpp"], {'c': [''], 'Yu': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Zp", "main.cpp"], {'c': [''], 'Zp': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fa", "main.cpp"], {'c': [''], 'Fa': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fd", "main.cpp"], {'c': [''], 'Fd': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fe", "main.cpp"], {'c': [''], 'Fe': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fi", "main.cpp"], {'c': [''], 'Fi': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fm", "main.cpp"], {'c': [''], 'Fm': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fo", "main.cpp"], {'c': [''], 'Fo': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fp", "main.cpp"], {'c': [''], 'Fp': ['']}, ['main.cpp'])

        # Type 3 (/NAME[ ]parameter) - Required argument parameters with optional space
        # get space
        self._testArgInfiles(["/c", "/FI", "99", "main.cpp"], {'c': [''], 'FI': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/U", "99", "main.cpp"], {'c': [''], 'U': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/I", "99", "main.cpp"], {'c': [''], 'I': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/F", "99", "main.cpp"], {'c': [''], 'F': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/FU", "99", "main.cpp"], {'c': [''], 'FU': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/w1", "99", "main.cpp"], {'c': [''], 'w1': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/w2", "99", "main.cpp"], {'c': [''], 'w2': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/w3", "99", "main.cpp"], {'c': [''], 'w3': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/w4", "99", "main.cpp"], {'c': [''], 'w4': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/wd", "99", "main.cpp"], {'c': [''], 'wd': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/we", "99", "main.cpp"], {'c': [''], 'we': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/wo", "99", "main.cpp"], {'c': [''], 'wo': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/AI", "99", "main.cpp"], {'c': [''], 'AI': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/D", "99", "main.cpp"], {'c': [''], 'D': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/V", "99", "main.cpp"], {'c': [''], 'V': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Tc", "99", "main.cpp"], {'c': [''], 'Tc': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Tp", "99", "main.cpp"], {'c': [''], 'Tp': ['99']}, ['main.cpp'])
        # don't get space
        self._testArgInfiles(["/c", "/FI99", "main.cpp"], {'c': [''], 'FI': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/U99", "main.cpp"], {'c': [''], 'U': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/I99", "main.cpp"], {'c': [''], 'I': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/F99", "main.cpp"], {'c': [''], 'F': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/FU99", "main.cpp"], {'c': [''], 'FU': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/w199", "main.cpp"], {'c': [''], 'w1': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/w299", "main.cpp"], {'c': [''], 'w2': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/w399", "main.cpp"], {'c': [''], 'w3': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/w499", "main.cpp"], {'c': [''], 'w4': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/wd99", "main.cpp"], {'c': [''], 'wd': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/we99", "main.cpp"], {'c': [''], 'we': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/wo99", "main.cpp"], {'c': [''], 'wo': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/AI99", "main.cpp"], {'c': [''], 'AI': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/D99", "main.cpp"], {'c': [''], 'D': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/V99", "main.cpp"], {'c': [''], 'V': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Tc99", "main.cpp"], {'c': [''], 'Tc': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Tp99", "main.cpp"], {'c': [''], 'Tp': ['99']}, ['main.cpp'])

        # Type 4 (/NAME parameter) - Forced space
        # Some documented, but non implemented



class TestBasenameUtility(unittest.TestCase):
    def testBasenameWithoutExtension(self):
        self.assertEqual(basenameWithoutExtension(r"README.asciidoc"), "README")
        self.assertEqual(basenameWithoutExtension(r"/home/user/README.asciidoc"), "README")
        self.assertEqual(basenameWithoutExtension(r"C:\Project\README.asciidoc"), "README")

        self.assertEqual(basenameWithoutExtension(r"READ ME.asciidoc"), "READ ME")
        self.assertEqual(basenameWithoutExtension(r"/home/user/READ ME.asciidoc"), "READ ME")
        self.assertEqual(basenameWithoutExtension(r"C:\Project\READ ME.asciidoc"), "READ ME")

        self.assertEqual(basenameWithoutExtension(r"README.asciidoc.tmp"), "README.asciidoc")
        self.assertEqual(basenameWithoutExtension(r"/home/user/README.asciidoc.tmp"), "README.asciidoc")
        self.assertEqual(basenameWithoutExtension(r"C:\Project\README.asciidoc.tmp"), "README.asciidoc")


class TestSplitCommandsFile(unittest.TestCase):
    def _genericTest(self, commandLine, expected):
        self.assertEqual(splitCommandsFile(commandLine), expected)

    def testEmpty(self):
        self._genericTest('', [])

    def testSimple(self):
        self._genericTest('/nologo', ['/nologo'])
        self._genericTest('/nologo /c', ['/nologo', '/c'])
        self._genericTest('/nologo /c -I.', ['/nologo', '/c', '-I.'])

    def testWhitespace(self):
        self._genericTest('-A -B    -C', ['-A', '-B', '-C'])
        self._genericTest('   -A -B -C', ['-A', '-B', '-C'])
        self._genericTest('-A -B -C   ', ['-A', '-B', '-C'])

    def testMicrosoftExamples(self):
        # https://msdn.microsoft.com/en-us/library/17w5ykft.aspx
        self._genericTest(r'"abc" d e', ['abc', 'd', 'e'])
        self._genericTest(r'a\\b d"e f"g h', [r'a\\b', 'de fg', 'h'])
        self._genericTest(r'a\\\"b c d', [r'a\"b', 'c', 'd'])
        self._genericTest(r'a\\\\"b c" d e', [r'a\\b c', 'd', 'e'])

    def testQuotesAroundArgument(self):
        self._genericTest(r'/Fo"C:\out dir\main.obj"', [r'/FoC:\out dir\main.obj'])
        self._genericTest(r'/c /Fo"C:\out dir\main.obj"', ['/c', r'/FoC:\out dir\main.obj'])
        self._genericTest(r'/Fo"C:\out dir\main.obj" /nologo', [r'/FoC:\out dir\main.obj', '/nologo'])
        self._genericTest(r'/c /Fo"C:\out dir\main.obj" /nologo', ['/c', r'/FoC:\out dir\main.obj', '/nologo'])

    def testDoubleQuoted(self):
        self._genericTest(r'"/Fo"something\main.obj""', [r'/Fosomething\main.obj'])
        self._genericTest(r'/c "/Fo"something\main.obj""', ['/c', r'/Fosomething\main.obj'])
        self._genericTest(r'"/Fo"something\main.obj"" /nologo', [r'/Fosomething\main.obj', '/nologo'])
        self._genericTest(r'/c "/Fo"something\main.obj"" /nologo', ['/c', r'/Fosomething\main.obj', '/nologo'])

    def testBackslashBeforeQuote(self):
        # Pathological cases of escaping the quote incorrectly.
        self._genericTest(r'/Fo"C:\out dir\"', [r'/FoC:\out dir"'])
        self._genericTest(r'/c /Fo"C:\out dir\"', ['/c', r'/FoC:\out dir"'])
        self._genericTest(r'/Fo"C:\out dir\" /nologo', [r'/FoC:\out dir" /nologo'])
        self._genericTest(r'/c /Fo"C:\out dir\" /nologo', ['/c', r'/FoC:\out dir" /nologo'])

        # Sane cases of escaping the backslash correctly.
        self._genericTest(r'/Fo"C:\out dir\\"', [r'/FoC:\out dir' + '\\'])
        self._genericTest(r'/c /Fo"C:\out dir\\"', ['/c', r'/FoC:\out dir' + '\\'])
        self._genericTest(r'/Fo"C:\out dir\\" /nologo', [r'/FoC:\out dir' + '\\', r'/nologo'])
        self._genericTest(r'/c /Fo"C:\out dir\\" /nologo', ['/c', r'/FoC:\out dir' + '\\', r'/nologo'])

    def testVyachselavCase(self):
        self._genericTest(
            r'"-IC:\Program files\Some library" -DX=1 -DVERSION=\"1.0\" -I..\.. -I"..\..\lib" -DMYPATH=\"C:\Path\"',
            [
                r'-IC:\Program files\Some library',
                r'-DX=1',
                r'-DVERSION="1.0"',
                r'-I..\..',
                r'-I..\..\lib',
                r'-DMYPATH="C:\Path"'
            ])

    def testLineEndings(self):
        self._genericTest('-A\n-B', ['-A', '-B'])
        self._genericTest('-A\r\n-B', ['-A', '-B'])
        self._genericTest('-A -B\r\n-C -D -E', ['-A', '-B', '-C', '-D', '-E'])

    def testInitialBackslash(self):
        self._genericTest(r'/Fo"C:\out dir\"', [r'/FoC:\out dir"'])
        self._genericTest(r'\foo.cpp', [r'\foo.cpp'])
        self._genericTest(r'/nologo \foo.cpp', [r'/nologo', r'\foo.cpp'])
        self._genericTest(r'\foo.cpp /c', [r'\foo.cpp', r'/c'])


class TestExpandCommandLine(unittest.TestCase):
    def _genericTest(self, commandLine, expected):
        with cd(os.path.join(ASSETS_DIR, "response-files")):
            self.assertEqual(expandCommandLine(commandLine), expected)

    def testNoResponseFile(self):
        self._genericTest(['-A', '-B'], ['-A', '-B'])

    def testMissingResponseFile(self):
        with self.assertRaises(FileNotFoundError):
            self._genericTest(['-A', '@no_such_file.rsp', '-B'], [])

    def testSingleResponseFile(self):
        self._genericTest(['-A', '@default_encoded.rsp', '-B'], ['-A', '/DPASSWORD=Käse', '/nologo', '-B'])

    def testMultipleResponseFile(self):
        self._genericTest(
            ['-A', '@default_encoded.rsp', '@utf16_encoded.rsp', '-B'],
            ['-A', '/DPASSWORD=Käse', '/nologo', '/DPASSWORD=Фёдор', '/IC:\\Users\\Миха́йлович', '-B']
        )

    def testNestedResponseFiles(self):
        self._genericTest(
            ['-A', '@nested_response_file.rsp', '-B'],
            ['-A', '/O2', '/DSOMETHING=foo', '/DANOTHERTHING=bar', '/nologo', '-B']
        )


class TestExtendCommandLineFromEnvironment(unittest.TestCase):
    def testEmpty(self):
        cmdLine, env = extendCommandLineFromEnvironment([], {})
        self.assertEqual(cmdLine, [])
        self.assertEqual(env, {})

    def testSimple(self):
        cmdLine, env = extendCommandLineFromEnvironment(['/nologo'], {'USER': 'ab'})
        self.assertEqual(cmdLine, ['/nologo'])
        self.assertEqual(env, {'USER': 'ab'})

    def testPrepend(self):
        cmdLine, env = extendCommandLineFromEnvironment(['/nologo'], {
            'USER': 'ab',
            'CL': '/MP',
        })
        self.assertEqual(cmdLine, ['/MP', '/nologo'])
        self.assertEqual(env, {'USER': 'ab'})

    def testPrependMultiple(self):
        cmdLine, _ = extendCommandLineFromEnvironment(['INPUT.C'], {
            'CL': r'/Zp2 /Ox /I\INCLUDE\MYINCLS \LIB\BINMODE.OBJ',
        })
        self.assertEqual(cmdLine, ['/Zp2', '/Ox', r'/I\INCLUDE\MYINCLS', r'\LIB\BINMODE.OBJ', 'INPUT.C'])

    def testAppend(self):
        cmdLine, env = extendCommandLineFromEnvironment(['/nologo'], {
            'USER': 'ab',
            '_CL_': 'file.c',
        })
        self.assertEqual(cmdLine, ['/nologo', 'file.c'])
        self.assertEqual(env, {'USER': 'ab'})

    def testAppendPrepend(self):
        cmdLine, env = extendCommandLineFromEnvironment(['/nologo'], {
            'USER': 'ab',
            'CL': '/MP',
            '_CL_': 'file.c',
        })
        self.assertEqual(cmdLine, ['/MP', '/nologo', 'file.c'])
        self.assertEqual(env, {'USER': 'ab'})


class TestJobCount(unittest.TestCase):
    CPU_CORES = multiprocessing.cpu_count()

    # TODO: is this even needed?
    def testCpuCuresPlausibility(self):
        # 1 <= CPU_CORES <= 32
        self.assertGreaterEqual(self.CPU_CORES, 1)
        self.assertLessEqual(self.CPU_CORES, 32)

    def testJobCount(self):
        # Basic parsing
        actual = jobCount(["/MP1"])
        self.assertEqual(actual, 1)
        actual = jobCount(["/MP100"])
        self.assertEqual(actual, 100)

        # Without optional max process value
        actual = jobCount(["/MP"])
        self.assertEqual(actual, self.CPU_CORES)

        # Invalid inputs
        actual = jobCount(["/MP100.0"])
        self.assertEqual(actual, 1)
        actual = jobCount(["/MP-100"])
        self.assertEqual(actual, 1)
        actual = jobCount(["/MPfoo"])
        self.assertEqual(actual, 1)

        # Multiple values
        actual = jobCount(["/MP1", "/MP44"])
        self.assertEqual(actual, 44)
        actual = jobCount(["/MP1", "/MP44", "/MP"])
        self.assertEqual(actual, self.CPU_CORES)

        # Find /MP in mixed command line
        actual = jobCount(["/c", "/nologo", "/MP44"])
        self.assertEqual(actual, 44)
        actual = jobCount(["/c", "/nologo", "/MP44", "mysource.cpp"])
        self.assertEqual(actual, 44)
        actual = jobCount(["/MP2", "/c", "/nologo", "/MP44", "mysource.cpp"])
        self.assertEqual(actual, 44)
        actual = jobCount(["/MP2", "/c", "/MP44", "/nologo", "/MP", "mysource.cpp"])
        self.assertEqual(actual, self.CPU_CORES)
