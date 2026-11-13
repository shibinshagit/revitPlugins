using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.Loader;

namespace NetcoreHost
{
    // Runs window_check.py on pyRevit's .NET 8 IronPython 2.7 engine.
    // Usage: netcore_host <engineDir> <driver.py> <lib> <samples> <out>
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            if (args.Length != 5)
            {
                Console.Error.WriteLine("usage: netcore_host <engineDir> <driver.py> <lib> <samples> <out>");
                return 2;
            }
            string engineDir = args[0];

            AssemblyLoadContext.Default.Resolving += (context, name) =>
            {
                string candidate = Path.Combine(engineDir, name.Name + ".dll");
                return File.Exists(candidate) ? context.LoadFromAssemblyPath(candidate) : null;
            };

            Assembly Load(string file) =>
                AssemblyLoadContext.Default.LoadFromAssemblyPath(Path.Combine(engineDir, file));

            Assembly scripting = Load("pyRevitLabs.Microsoft.Scripting.dll");
            Load("pyRevitLabs.Microsoft.Dynamic.dll");
            Assembly ironPython = Load("pyRevitLabs.IronPython.dll");
            Assembly modules = Load("pyRevitLabs.IronPython.Modules.dll");
            Assembly loader = Load("pyRevitLoader.dll");

            object engine = ironPython.GetType("IronPython.Hosting.Python", true)
                .GetMethod("CreateEngine", Type.EmptyTypes)
                .Invoke(null, null);
            Type engineType = engine.GetType();
            object scope = engineType.GetMethod("CreateScope", Type.EmptyTypes).Invoke(engine, null);
            MethodInfo setVariable = scope.GetType().GetMethod("SetVariable", new[] { typeof(string), typeof(object) });

            void Set(string name, object value) => setVariable.Invoke(scope, new[] { name, value });
            Set("LIB", args[2]);
            Set("SAMPLES", args[3]);
            Set("OUT", args[4]);
            Set("LOADER", loader);
            Set("MODULES", modules);
            Directory.CreateDirectory(args[4]);

            MethodInfo executeFile = engineType.GetMethods()
                .First(m => m.Name == "ExecuteFile" && m.GetParameters().Length == 2);
            try
            {
                executeFile.Invoke(engine, new[] { args[1], scope });
            }
            catch (TargetInvocationException wrapped)
            {
                Exception error = wrapped.InnerException ?? wrapped;
                Type operations = scripting.GetType("Microsoft.Scripting.Hosting.ExceptionOperations", true);
                object service = engineType.GetMethod("GetService")
                    .MakeGenericMethod(operations)
                    .Invoke(engine, new object[] { Array.Empty<object>() });
                string text = (string)operations.GetMethod("FormatException", new[] { typeof(Exception) })
                    .Invoke(service, new object[] { error });
                Console.WriteLine(text);
                return 1;
            }

            MethodInfo getVariable = scope.GetType().GetMethods()
                .First(m => m.Name == "GetVariable" && !m.IsGenericMethod && m.GetParameters().Length == 1);
            return Convert.ToInt32(getVariable.Invoke(scope, new object[] { "FAILURES" }));
        }
    }
}
