# !! REQUIRES environment variable TMP_OUT_DIR to be set
# before launching makefile, f.x. in PowerShell:
# > $env:TMP_OUT_DIR="${env:TMP}omgwtf\"
# > nmake

RM = @del /q

OBJS = $(TMP_OUT_DIR)myapp.obj $(TMP_OUT_DIR)applib.obj
APP = $(TMP_OUT_DIR)myapp.exe

# List all stable header files in the STABLEHDRS macro.
STABLEHDRS = stable.h another.h

# List the final header file to be precompiled here:
BOUNDRY = stable.h

# List header files under development here:
UNSTABLEHDRS = unstable.h

CPPFLAGS  = /nologo /EHsc /c /W3 /Gs /MT /Fo$(TMP_OUT_DIR) /Fp$(TMP_OUT_DIR)stable.pch
LINKFLAGS =
LIBS      =

$(APP): $(OBJS)
    link $(LINKFLAGS) /OUT:$(APP) $(OBJS) $(LIBS)

# Compile myapp
$(TMP_OUT_DIR)myapp.obj  : myapp.cpp $(UNSTABLEHDRS) $(TMP_OUT_DIR)stable.pch
    $(CPP) $(CPPFLAGS) /Yu$(BOUNDRY) myapp.cpp

# Compile applib
#the_applib.obj : applib.cpp $(UNSTABLEHDRS) stable.pch
#    $(CPP) $(CPPFLAGS) /Yu$(BOUNDRY) /Fothe_applib.obj applib.cpp

# Compile headers
$(TMP_OUT_DIR)stable.pch : $(STABLEHDRS)
    $(CPP) $(CPPFLAGS) /Yc$(BOUNDRY) applib.cpp

.PHONY: clean

clean:
    $(RM) $(OBJS) $(APP) $(TMP_OUT_DIR)stable.pch
