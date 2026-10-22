var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();
app.MapGet("/", () => "Hello from C# on myvps!");
app.MapGet("/health", () => Results.Json(new { status = "ok", runtime = "dotnet", service = "myvps" }));
app.Run();
