/*
 * Lanzador de Luci.app
 *
 * macOS concede el permiso de micrófono a aplicaciones (.app), no a scripts sueltos.
 * Este ejecutable es el "cuerpo" de Luci.app: arranca el servicio de Luci en Python
 * como proceso hijo, de modo que el permiso de la app se aplica también al hijo.
 *
 * La ruta del intérprete de Python está en Contents/Resources/python (la escribe
 * `luci configurar`).
 */
#include <errno.h>
#include <libgen.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
static pid_t hijo = 0;

static void reenviar(int senal) {
    if (hijo > 0) kill(hijo, senal);
}

int main(void) {
    char ejecutable[4096];
    uint32_t largo = sizeof ejecutable;
    if (_NSGetExecutablePath(ejecutable, &largo) != 0) {
        fprintf(stderr, "luci: ruta del ejecutable demasiado larga\n");
        return 1;
    }
    char ruta[4096];
    snprintf(ruta, sizeof ruta, "%s/../Resources/python", dirname(ejecutable));

    FILE *f = fopen(ruta, "r");
    if (!f) {
        fprintf(stderr, "luci: no encuentro %s (ejecuta `luci configurar`)\n", ruta);
        return 1;
    }
    char python[4096] = {0};
    if (!fgets(python, sizeof python, f)) {
        fclose(f);
        fprintf(stderr, "luci: %s está vacío\n", ruta);
        return 1;
    }
    fclose(f);
    python[strcspn(python, "\r\n")] = '\0';

    char *argumentos[] = {python, "-m", "luci", "servicio", NULL};
    signal(SIGTERM, reenviar);
    signal(SIGINT, reenviar);
    signal(SIGHUP, reenviar);

    int error = posix_spawn(&hijo, python, NULL, NULL, argumentos, environ);
    if (error != 0) {
        fprintf(stderr, "luci: no pude iniciar %s: %s\n", python, strerror(error));
        return 1;
    }
    int estado = 0;
    while (waitpid(hijo, &estado, 0) < 0) {
        if (errno != EINTR) return 1;
    }
    return WIFEXITED(estado) ? WEXITSTATUS(estado) : 1;
}
